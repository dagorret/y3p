# Generado por Django 6.1.2 el 2026-11-11 21:25

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='AdmissionCourseRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order', models.IntegerField(default=10, help_text='Prioridad de evaluación. Primer match gana.')),
                ('career_code', models.CharField(blank=True, help_text='Código carrera (ej: 2, 3, 4, 7, 8) o NULL para comodín.', max_length=20, null=True)),
                ('modality', models.CharField(blank=True, help_text="'1', '2', 'P', 'D' o NULL para comodín.", max_length=10, null=True)),
                ('commission_pattern', models.CharField(blank=True, help_text="Regex o texto de comisión elegida (ej: 'COMISION 1', 'COMISION 2') o NULL.", max_length=50, null=True)),
                ('target_courses', models.CharField(help_text="Shortnames de Moodle separados por coma (ej: 'I3,I4,I5,I6,I1').", max_length=255)),
                ('target_group', models.CharField(blank=True, help_text='Nombre de grupo/comisión en Moodle a asignar.', max_length=100, null=True)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Regla de cursos de ingreso',
                'verbose_name_plural': 'Reglas de cursos de ingreso',
                'ordering': ['order', 'id'],
            },
        ),
    ]
