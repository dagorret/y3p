from django.db import models


class MockAzureUser(models.Model):
    """Usuario simulado de Microsoft Graph / Entra ID."""

    upn = models.CharField(max_length=150, unique=True, db_index=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    document_number = models.CharField(max_length=20, db_index=True)
    is_active = models.BooleanField(default=True)
    assigned_sku = models.CharField(max_length=100, blank=True, null=True)
    password_profile_force_change = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


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
