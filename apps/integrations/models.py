"""Configuración persistida de conversión de ingreso a cursos pedagógicos."""

import re
import uuid
from datetime import time

from django.core.exceptions import ValidationError
from django.db import models


class TeamWorkspace(models.Model):
    """Inventario local de equipos gestionados y su correspondencia pedagógica."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_name = models.CharField(max_length=255)
    owner_upn = models.EmailField(max_length=150)
    moodle_shortname = models.CharField(max_length=100, blank=True)
    career_code = models.CharField(max_length=20, blank=True)
    commission = models.CharField(max_length=100, blank=True)
    members_snapshot = models.JSONField(default=list, blank=True)
    moodle_snapshot = models.JSONField(default=list, blank=True)
    observed_at = models.DateTimeField(null=True, blank=True)
    moodle_observed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_name', 'id']


class AdmissionCourseRule(models.Model):
    order = models.IntegerField(default=10, help_text="Prioridad de evaluación. Primer match gana.")
    career_code = models.CharField(
        max_length=20, blank=True, null=True,
        help_text="Código carrera (ej: 2, 3, 4, 7, 8) o NULL para comodín.",
    )
    modality = models.CharField(
        max_length=10, blank=True, null=True,
        help_text="'1', '2', 'P', 'D' o NULL para comodín.",
    )
    commission_pattern = models.CharField(
        max_length=50, blank=True, null=True,
        help_text="Regex o texto de comisión elegida (ej: 'COMISION 1', 'COMISION 2') o NULL.",
    )
    target_courses = models.CharField(
        max_length=255,
        help_text="Shortnames de Moodle separados por coma (ej: 'I3,I4,I5,I6,I1').",
    )
    target_group = models.CharField(
        max_length=100, blank=True, null=True,
        help_text="Nombre de grupo/comisión en Moodle a asignar.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'id']
        verbose_name = "Regla de cursos de ingreso"
        verbose_name_plural = "Reglas de cursos de ingreso"

    def clean(self):
        super().clean()
        if self.commission_pattern and self.commission_pattern.strip():
            try:
                re.compile(self.commission_pattern.strip(), re.IGNORECASE)
            #FINTEGRATIONSMODELS001-8001: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
            except re.error as error:
                #FINTEGRATIONSMODELS002-8002: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
                raise ValidationError({'commission_pattern': 'La expresión regular no es válida.'}) from error
        if not any(item.strip() for item in self.target_courses.split(',')):
            #FINTEGRATIONSMODELS003-8003: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
            raise ValidationError({'target_courses': 'Debe indicar al menos un shortname.'})

    @classmethod
    def resolve_courses(cls, career: str, modality: str, commission: str) -> tuple[list[str], str | None]:
        """Primera coincidencia activa; expresión completa sin distinguir mayúsculas.

        Los campos career y modality se comparan como texto sin espacios exteriores.
        NULL/vacío en la regla es comodín. Sin coincidencias retorna ([], None).
        """

        from apps.integrations.exceptions import ConfigurationError

        career, modality, commission = (str(value or '').strip() for value in (career, modality, commission))
        for rule in cls.objects.filter(is_active=True).order_by('order', 'id'):
            if rule.career_code and rule.career_code.strip() and rule.career_code.strip() != career:
                continue
            if rule.modality and rule.modality.strip() and rule.modality.strip() != modality:
                continue
            pattern = (rule.commission_pattern or '').strip()
            try:
                if pattern and re.fullmatch(pattern, commission, re.IGNORECASE) is None:
                    continue
            #FINTEGRATIONSMODELS004-8004: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
            except re.error as error:
                #FINTEGRATIONSMODELS005-8005: Origina ConfigurationError; el llamador conserva la causa y decide su tratamiento.
                raise ConfigurationError('Expresión de comisión inválida en regla de ingreso.') from error
            courses = list(dict.fromkeys(item.strip() for item in rule.target_courses.split(',') if item.strip()))
            if not courses:
                #FINTEGRATIONSMODELS006-8006: Origina ConfigurationError; el llamador conserva la causa y decide su tratamiento.
                raise ConfigurationError('La regla de ingreso no contiene cursos Moodle.')
            return courses, (rule.target_group or '').strip() or None
        return [], None

    def __str__(self):
        return f"{self.order}: {self.career_code or '*'} → {self.target_courses}"


class TeamsWorkspaceMirror(models.Model):
    """Equipo remoto consultable localmente, con contadores e índices analíticos."""

    team_id = models.CharField('Identificador Teams', max_length=150, unique=True)
    display_name = models.CharField('Nombre', max_length=255)
    mail_nickname = models.CharField('Alias de correo', max_length=255, blank=True)
    description = models.TextField('Descripción', blank=True)
    career_code = models.CharField('Carrera', max_length=20, blank=True)
    commission = models.CharField('Comisión', max_length=100, blank=True)
    owners_count = models.PositiveIntegerField('Propietarios', default=0)
    members_count = models.PositiveIntegerField('Miembros', default=0, db_index=True)
    synced_at = models.DateTimeField('Sincronizado el')

    class Meta:
        ordering = ('display_name', 'team_id')
        verbose_name = 'Equipo espejo'
        verbose_name_plural = 'Equipos espejo'


class TeamsChannelMirror(models.Model):
    """Canal asociado por clave foránea; no acredita acceso individual a canales privados."""

    channel_id = models.CharField('Identificador del canal', max_length=255, unique=True)
    workspace = models.ForeignKey(TeamsWorkspaceMirror, verbose_name='Equipo', on_delete=models.CASCADE, related_name='channels')
    display_name = models.CharField('Nombre', max_length=255)
    membership_type = models.CharField('Tipo de membresía', max_length=20, choices=(('standard', 'Estándar'), ('private', 'Privado')))
    synced_at = models.DateTimeField('Sincronizado el')

    class Meta:
        ordering = ('display_name', 'channel_id')
        constraints = [models.CheckConstraint(condition=models.Q(membership_type__in=['standard', 'private']), name='ck_channel_mirror_type')]


class TeamsMemberMirror(models.Model):
    """Pivot relacional en lugar de JSON para pertenencias y análisis indexados.

    La unicidad equipo/UPN evita duplicados. Los índices identidad/equipo y
    equipo/rol permiten consultar pertenencias y propietarios mediante el ORM,
    sin recorrer documentos JSON ni perder integridad referencial. Son índices
    B-tree: aceleran búsquedas y agregaciones, sin prometer complejidad O(1).
    Los invitados conservan su membresía aunque no exista una identidad local.
    """

    workspace = models.ForeignKey(TeamsWorkspaceMirror, verbose_name='Equipo', on_delete=models.CASCADE, related_name='members')
    identity = models.ForeignKey('people.Identity', verbose_name='Identidad', on_delete=models.SET_NULL, null=True, blank=True, related_name='teams_memberships')
    azure_user_id = models.CharField('Identificador Azure', max_length=150, db_index=True)
    upn = models.CharField('UPN', max_length=255, db_index=True)
    display_name = models.CharField('Nombre', max_length=255)
    role = models.CharField('Rol', max_length=10, choices=(('owner', 'Propietario'), ('member', 'Miembro')), db_index=True)
    synced_at = models.DateTimeField('Sincronizado el')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['workspace', 'upn'], name='uniq_teams_member_workspace_upn'),
            models.CheckConstraint(condition=models.Q(role__in=['owner', 'member']), name='ck_member_mirror_role'),
        ]
        indexes = [models.Index(fields=['identity', 'workspace'], name='ix_member_identity_workspace'),
                   models.Index(fields=['workspace', 'role'], name='ix_member_workspace_role')]


class MoodleCourseMirror(models.Model):
    """Catálogo de cursos remoto, independiente de sus matrículas individuales."""

    course_id = models.PositiveBigIntegerField('Identificador Moodle', unique=True)
    shortname = models.CharField('Nombre corto', max_length=255, db_index=True)
    fullname = models.CharField('Nombre completo', max_length=255)
    category_name = models.CharField('Categoría', max_length=255, blank=True)
    visible = models.BooleanField('Visible', default=True)
    enrolled_count = models.PositiveIntegerField('Matriculados activos', default=0)
    synced_at = models.DateTimeField('Sincronizado el')

    class Meta:
        ordering = ('shortname', 'course_id')


class CatalogSyncState(models.Model):
    """Agenda semanal y resultado por origen, con arrendamiento contra ejecuciones simultáneas."""

    source = models.CharField('Origen', max_length=10, unique=True, choices=(('TEAMS', 'Teams'), ('MOODLE', 'Moodle')))
    schedule_weekday = models.PositiveSmallIntegerField('Día semanal', default=5)
    schedule_hour = models.TimeField('Hora local', default=time(1, 0))
    last_run_at = models.DateTimeField('Última ejecución', null=True, blank=True)
    next_run_at = models.DateTimeField('Próxima ejecución', null=True, blank=True, db_index=True)
    last_status = models.CharField('Último resultado', max_length=10, blank=True, choices=(('SUCCEEDED', 'Completado'), ('FAILED', 'Fallido')))
    items_synced = models.PositiveIntegerField('Elementos sincronizados', default=0)
    last_error = models.TextField('Último error', blank=True)
    run_token = models.UUIDField('Identificador de ejecución', null=True, blank=True)
    lock_expires_at = models.DateTimeField('Vencimiento del arrendamiento', null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(schedule_weekday__gte=0, schedule_weekday__lte=6), name='ck_catalog_weekday')]
