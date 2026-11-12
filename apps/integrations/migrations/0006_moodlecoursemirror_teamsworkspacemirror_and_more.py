# Generado por Django 6.1.2 el 2026-11-12 03:44

import datetime
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0005_backfill_team_workspaces'),
        ('people', '0014_digitalaccount_observations_outboxevent_requested_by_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='MoodleCourseMirror',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('course_id', models.PositiveBigIntegerField(unique=True, verbose_name='Identificador Moodle')),
                ('shortname', models.CharField(db_index=True, max_length=255, verbose_name='Nombre corto')),
                ('fullname', models.CharField(max_length=255, verbose_name='Nombre completo')),
                ('category_name', models.CharField(blank=True, max_length=255, verbose_name='Categoría')),
                ('visible', models.BooleanField(default=True, verbose_name='Visible')),
                ('enrolled_count', models.PositiveIntegerField(default=0, verbose_name='Matriculados activos')),
                ('synced_at', models.DateTimeField(verbose_name='Sincronizado el')),
            ],
            options={
                'ordering': ('shortname', 'course_id'),
            },
        ),
        migrations.CreateModel(
            name='TeamsWorkspaceMirror',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('team_id', models.CharField(max_length=150, unique=True, verbose_name='Identificador Teams')),
                ('display_name', models.CharField(max_length=255, verbose_name='Nombre')),
                ('mail_nickname', models.CharField(blank=True, max_length=255, verbose_name='Alias de correo')),
                ('description', models.TextField(blank=True, verbose_name='Descripción')),
                ('career_code', models.CharField(blank=True, max_length=20, verbose_name='Carrera')),
                ('commission', models.CharField(blank=True, max_length=100, verbose_name='Comisión')),
                ('owners_count', models.PositiveIntegerField(default=0, verbose_name='Propietarios')),
                ('members_count', models.PositiveIntegerField(db_index=True, default=0, verbose_name='Miembros')),
                ('synced_at', models.DateTimeField(verbose_name='Sincronizado el')),
            ],
            options={
                'verbose_name': 'Equipo espejo',
                'verbose_name_plural': 'Equipos espejo',
                'ordering': ('display_name', 'team_id'),
            },
        ),
        migrations.CreateModel(
            name='CatalogSyncState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source', models.CharField(choices=[('TEAMS', 'Teams'), ('MOODLE', 'Moodle')], max_length=10, unique=True, verbose_name='Origen')),
                ('schedule_weekday', models.PositiveSmallIntegerField(default=5, verbose_name='Día semanal')),
                ('schedule_hour', models.TimeField(default=datetime.time(1, 0), verbose_name='Hora local')),
                ('last_run_at', models.DateTimeField(blank=True, null=True, verbose_name='Última ejecución')),
                ('next_run_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='Próxima ejecución')),
                ('last_status', models.CharField(blank=True, choices=[('SUCCEEDED', 'Completado'), ('FAILED', 'Fallido')], max_length=10, verbose_name='Último resultado')),
                ('items_synced', models.PositiveIntegerField(default=0, verbose_name='Elementos sincronizados')),
                ('last_error', models.TextField(blank=True, verbose_name='Último error')),
                ('run_token', models.UUIDField(blank=True, null=True, verbose_name='Identificador de ejecución')),
                ('lock_expires_at', models.DateTimeField(blank=True, null=True, verbose_name='Vencimiento del arrendamiento')),
            ],
            options={
                'constraints': [models.CheckConstraint(condition=models.Q(('schedule_weekday__gte', 0), ('schedule_weekday__lte', 6)), name='ck_catalog_weekday')],
            },
        ),
        migrations.CreateModel(
            name='TeamsMemberMirror',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('azure_user_id', models.CharField(db_index=True, max_length=150, verbose_name='Identificador Azure')),
                ('upn', models.CharField(db_index=True, max_length=255, verbose_name='UPN')),
                ('display_name', models.CharField(max_length=255, verbose_name='Nombre')),
                ('role', models.CharField(choices=[('owner', 'Propietario'), ('member', 'Miembro')], db_index=True, max_length=10, verbose_name='Rol')),
                ('synced_at', models.DateTimeField(verbose_name='Sincronizado el')),
                ('identity', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='teams_memberships', to='people.identity', verbose_name='Identidad')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='members', to='integrations.teamsworkspacemirror', verbose_name='Equipo')),
            ],
            options={
                'indexes': [models.Index(fields=['identity', 'workspace'], name='ix_member_identity_workspace'), models.Index(fields=['workspace', 'role'], name='ix_member_workspace_role')],
                'constraints': [models.UniqueConstraint(fields=('workspace', 'upn'), name='uniq_teams_member_workspace_upn'), models.CheckConstraint(condition=models.Q(('role__in', ['owner', 'member'])), name='ck_member_mirror_role')],
            },
        ),
        migrations.CreateModel(
            name='TeamsChannelMirror',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel_id', models.CharField(max_length=255, unique=True, verbose_name='Identificador del canal')),
                ('display_name', models.CharField(max_length=255, verbose_name='Nombre')),
                ('membership_type', models.CharField(choices=[('standard', 'Estándar'), ('private', 'Privado')], max_length=20, verbose_name='Tipo de membresía')),
                ('synced_at', models.DateTimeField(verbose_name='Sincronizado el')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='channels', to='integrations.teamsworkspacemirror', verbose_name='Equipo')),
            ],
            options={
                'ordering': ('display_name', 'channel_id'),
                'constraints': [models.CheckConstraint(condition=models.Q(('membership_type__in', ['standard', 'private'])), name='ck_channel_mirror_type')],
            },
        ),
    ]
