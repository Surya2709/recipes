import io
import json
import re

import requests
from annoying.decorators import ajax_request
from annoying.functions import get_object_or_None
from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpResponse, FileResponse, JsonResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from icalendar import Calendar, Event
from rest_framework import viewsets, permissions
from rest_framework.exceptions import APIException
from rest_framework.mixins import RetrieveModelMixin, UpdateModelMixin, ListModelMixin

from cookbook.helper.permission_helper import group_required, CustomIsOwner, CustomIsAdmin, CustomIsUser
from cookbook.helper.recipe_url_import import get_from_html
from cookbook.models import Recipe, Sync, Storage, CookLog, MealPlan, MealType, ViewLog, UserPreference, RecipeBook, RecipeIngredient, Ingredient
from cookbook.provider.dropbox import Dropbox
from cookbook.provider.nextcloud import Nextcloud
from cookbook.serializer import MealPlanSerializer, MealTypeSerializer, RecipeSerializer, ViewLogSerializer, UserNameSerializer, UserPreferenceSerializer, RecipeBookSerializer, RecipeIngredientSerializer, IngredientSerializer


class UserNameViewSet(viewsets.ModelViewSet):
    """
    list:
    optional parameters

    - **filter_list**: array of user id's to get names for
    """
    queryset = User.objects.all()
    serializer_class = UserNameSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get']

    def get_queryset(self):
        queryset = User.objects.all()
        try:
            filter_list = self.request.query_params.get('filter_list', None)
            if filter_list is not None:
                queryset = queryset.filter(pk__in=json.loads(filter_list))
        except ValueError as e:
            raise APIException(_('Parameter filter_list incorrectly formatted'))

        return queryset


class UserPreferenceViewSet(viewsets.ModelViewSet):
    queryset = UserPreference.objects.all()
    serializer_class = UserPreferenceSerializer
    permission_classes = [CustomIsOwner, ]

    def perform_create(self, serializer):
        if UserPreference.objects.filter(user=self.request.user).exists():
            raise APIException(_('Preference for given user already exists'))
        serializer.save(user=self.request.user)

    def get_queryset(self):
        if self.request.user.is_superuser:
            return self.queryset
        return self.queryset.filter(user=self.request.user)


class RecipeBookViewSet(RetrieveModelMixin, UpdateModelMixin, ListModelMixin, viewsets.GenericViewSet):
    queryset = RecipeBook.objects.all()
    serializer_class = RecipeBookSerializer
    permission_classes = [CustomIsOwner, CustomIsAdmin]

    def get_queryset(self):
        if self.request.user.is_superuser:
            return self.queryset
        return self.queryset.filter(created_by=self.request.user)


class MealPlanViewSet(viewsets.ModelViewSet):
    """
    list:
    optional parameters

    - **html_week**: filter for a calendar week (format 2020-W24 as html input type week)

    """
    queryset = MealPlan.objects.all()
    serializer_class = MealPlanSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = MealPlan.objects.filter(Q(created_by=self.request.user) | Q(shared=self.request.user)).distinct().all()
        week = self.request.query_params.get('html_week', None)
        if week is not None:
            y, w = week.replace('-W', ' ').split()
            queryset = queryset.filter(date__week=w, date__year=y)
        return queryset


class MealTypeViewSet(viewsets.ModelViewSet):
    """
    list:
    returns list of meal types created by the requesting user ordered by the order field
    """
    queryset = MealType.objects.order_by('order').all()
    serializer_class = MealTypeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = MealType.objects.order_by('order', 'id').filter(created_by=self.request.user).all()
        return queryset


class RecipeViewSet(viewsets.ModelViewSet):
    """
    list:
    optional parameters

    - **query**: search a recipe for a string contained in the recipe name (case in-sensitive)
    - **limit**: limits the amount of returned recipes
    """
    queryset = Recipe.objects.all()
    serializer_class = RecipeSerializer
    permission_classes = [permissions.IsAuthenticated]  # TODO split read and write permission for meal plan guest

    def get_queryset(self):
        queryset = Recipe.objects.all()
        query = self.request.query_params.get('query', None)
        if query is not None:
            queryset = queryset.filter(name__icontains=query)

        limit = self.request.query_params.get('limit', None)
        if limit is not None:
            queryset = queryset[:int(limit)]
        return queryset


class RecipeIngredientViewSet(viewsets.ModelViewSet):
    queryset = RecipeIngredient.objects.all()
    serializer_class = RecipeIngredientSerializer
    permission_classes = [CustomIsUser]


class IngredientViewSet(viewsets.ModelViewSet):
    queryset = Ingredient.objects.all()
    serializer_class = IngredientSerializer
    permission_classes = [CustomIsUser]


class ViewLogViewSet(viewsets.ModelViewSet):
    queryset = ViewLog.objects.all()
    serializer_class = ViewLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = ViewLog.objects.filter(created_by=self.request.user).all()[:5]
        return queryset


# -------------- non django rest api views --------------------

def get_recipe_provider(recipe):
    if recipe.storage.method == Storage.DROPBOX:
        return Dropbox
    elif recipe.storage.method == Storage.NEXTCLOUD:
        return Nextcloud
    else:
        raise Exception('Provider not implemented')


def update_recipe_links(recipe):
    if not recipe.link:
        recipe.link = get_recipe_provider(recipe).get_share_link(recipe)  # TODO response validation in apis

    recipe.save()


@group_required('user')
def get_external_file_link(request, recipe_id):
    recipe = Recipe.objects.get(id=recipe_id)
    if not recipe.link:
        update_recipe_links(recipe)

    return HttpResponse(recipe.link)


@group_required('user')
def get_recipe_file(request, recipe_id):
    recipe = Recipe.objects.get(id=recipe_id)
    if not recipe.cors_link:
        update_recipe_links(recipe)

    return FileResponse(get_recipe_provider(recipe).get_file(recipe))


@group_required('user')
def sync_all(request):
    monitors = Sync.objects.filter(active=True)

    error = False
    for monitor in monitors:
        if monitor.storage.method == Storage.DROPBOX:
            ret = Dropbox.import_all(monitor)
            if not ret:
                error = True
        if monitor.storage.method == Storage.NEXTCLOUD:
            ret = Nextcloud.import_all(monitor)
            if not ret:
                error = True

    if not error:
        messages.add_message(request, messages.SUCCESS, _('Sync successful!'))
        return redirect('list_recipe_import')
    else:
        messages.add_message(request, messages.ERROR, _('Error synchronizing with Storage'))
        return redirect('list_recipe_import')


@group_required('user')
@ajax_request
def log_cooking(request, recipe_id):
    recipe = get_object_or_None(Recipe, id=recipe_id)
    if recipe:
        log = CookLog.objects.create(created_by=request.user, recipe=recipe)
        servings = request.GET['s'] if 's' in request.GET else None
        if servings and re.match(r'^([1-9])+$', servings):
            log.servings = int(servings)

        rating = request.GET['r'] if 'r' in request.GET else None
        if rating and re.match(r'^([1-9])+$', rating):
            log.rating = int(rating)
        log.save()
        return {'msg': 'updated successfully'}

    return {'error': 'recipe does not exist'}


@group_required('user')
def get_plan_ical(request, html_week):
    queryset = MealPlan.objects.filter(Q(created_by=request.user) | Q(shared=request.user)).distinct().all()

    y, w = html_week.replace('-W', ' ').split()
    queryset = queryset.filter(date__week=w, date__year=y)

    cal = Calendar()

    for p in queryset:
        event = Event()
        event['uid'] = p.id
        event.add('dtstart', p.date)
        event.add('dtend', p.date)
        event['summary'] = f'{p.meal_type.name}: {p.get_label()}'
        event['description'] = p.note
        cal.add_component(event)

    response = FileResponse(io.BytesIO(cal.to_ical()))
    response["Content-Disposition"] = f'attachment; filename=meal_plan_{html_week}.ics'

    return response


@group_required('user')
def recipe_from_url(request, url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.106 Safari/537.36'}
    try:
        response = requests.get(url, headers=headers)
    except requests.exceptions.ConnectionError:
        return JsonResponse({'error': True, 'msg': _('The requested page could not be found.')}, status=400)

    if response.status_code == 403:
        return JsonResponse({'error': True, 'msg': _('The requested page refused to provide any information (Status Code 403).')}, status=400)
    return get_from_html(response.text, url)
