import uuid

from django.db import models


class MockAzureUser(models.Model):
    """Usuario simulado de Microsoft Graph / Entra ID."""

    upn = models.CharField(max_length=150, unique=True, db_index=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150, blank=True)
    document_number = models.CharField(max_length=20, db_index=True, blank=True)
    is_active = models.BooleanField(default=True)
    assigned_sku = models.CharField(max_length=100, blank=True, null=True)
    password_profile_force_change = models.BooleanField(default=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MockTeam(models.Model):
    """Equipo educativo local con propietario institucional explícito."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(MockAzureUser, on_delete=models.PROTECT, related_name='owned_teams')


class MockTeamMember(models.Model):
    team = models.ForeignKey(MockTeam, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(MockAzureUser, on_delete=models.CASCADE)
    is_owner = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['team', 'user'], name='uq_mock_team_member')]


class MockMoodleUser(models.Model):
    """Usuario simulado de Moodle Web Services."""

    moodle_id = models.AutoField(primary_key=True)
    username = models.CharField(max_length=100, unique=True, db_index=True)
    email = models.EmailField(max_length=150)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    auth_plugin = models.CharField(max_length=50, default="oidc")
    suspended = models.BooleanField(default=False)


class MockMoodleCourse(models.Model):
    """Curso simulado de Moodle."""

    shortname = models.CharField(max_length=100, unique=True, db_index=True)
    fullname = models.CharField(max_length=255)


class MockMoodleEnrollment(models.Model):
    """Matrícula y comisión simuladas de Moodle."""

    user = models.ForeignKey(
        MockMoodleUser,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    course = models.ForeignKey(
        MockMoodleCourse,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    role_id = models.IntegerField(default=5)
    group_name = models.CharField(max_length=100, blank=True, null=True)
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "course"],
                name="unique_mock_moodle_user_course",
            ),
        ]


class MockSialRecord(models.Model):
    """Registro simulado del padrón de aspirantes e ingresantes UTI/SIAL."""

    dni = models.CharField(max_length=20, db_index=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    personal_email = models.EmailField(max_length=150)
    condition = models.CharField(max_length=50)
    career_code = models.CharField(max_length=10)
    modality = models.CharField(max_length=10)
    commission = models.CharField(max_length=50, blank=True, null=True)


class MockTeamChannel(models.Model):
    """Canal persistente de desarrollo para conservar el espejo tras una sincronización."""

    team = models.ForeignKey(MockTeam, verbose_name='Equipo', on_delete=models.CASCADE, related_name='channels')
    channel_id = models.CharField('Identificador del canal', max_length=255, unique=True)
    display_name = models.CharField('Nombre', max_length=255)
    membership_type = models.CharField('Tipo de membresía', max_length=20,
        choices=(('standard', 'Estándar'), ('private', 'Privado')))

    class Meta:
        verbose_name = 'Canal simulado'
        verbose_name_plural = 'Canales simulados'
        constraints = [models.CheckConstraint(condition=models.Q(membership_type__in=['standard', 'private']), name='ck_mock_channel_type')]
