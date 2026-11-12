# Generado por Django 6.1.2 el 2026-11-12 03:19

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AdminQuerySnippet',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150, verbose_name='Nombre')),
                ('query_expression', models.TextField(blank=True, max_length=2000, verbose_name='Fórmula')),
                ('filter_params', models.JSONField(blank=True, default=dict, verbose_name='Filtros complementarios')),
                ('is_shared', models.BooleanField(default=False, verbose_name='Compartido con administración')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Creado el')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Actualizado el')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='admin_query_snippets', to=settings.AUTH_USER_MODEL, verbose_name='Creado por')),
            ],
            options={
                'verbose_name': 'Vista guardada de administración',
                'verbose_name_plural': 'Vistas guardadas de administración',
                'ordering': ('name', 'pk'),
            },
        ),
    ]
