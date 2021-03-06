# Generated by Django 3.0.7 on 2020-06-11 13:09

from django.db import migrations
from django.db.models import Q


def migrate_meal_types(apps, schema_editor):
    MealPlan = apps.get_model('cookbook', 'MealPlan')
    MealType = apps.get_model('cookbook', 'MealType')
    User = apps.get_model('auth', 'User')

    for u in User.objects.all():
        for t in MealType.objects.filter(created_by=None).all():
            user_type = MealType.objects.create(
                name=t.name,
                created_by=u,
            )
            MealPlan.objects.filter(Q(created_by=u) and Q(meal_type=t)).update(meal_type=user_type)

    MealType.objects.filter(created_by=None).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('cookbook', '0049_mealtype_created_by'),
    ]

    operations = [
        migrations.RunPython(migrate_meal_types),
    ]
