"""Clientes locales persistentes para pruebas; nunca usan transportes de red.

Se inyectan explícitamente en el orquestador. ENABLE_LOCAL_MOCKS no redirige
los clientes reales ni implica dry-run: estas escrituras son en tablas mock.
"""

import re

from apps.integrations.dataclasses import (
    GraphUserResult, LicenseResult, MailDeliveryResult, MoodleEnrollmentResult,
    MoodleGroupResult, MoodleUserResult, PasswordSetResult,
)
from apps.integrations.exceptions import ResourceNotFoundError
from .models import MockAzureUser, MockMoodleCourse, MockMoodleEnrollment, MockMoodleUser
from .services import require_local_mocks


class MockGraphClient:
    def __init__(self):
        require_local_mocks()

    def _result(self, user, *, created=False):
        return GraphUserResult(str(user.pk), user.upn, f'{user.first_name} {user.last_name}', created)

    def get_user(self, upn):
        user = MockAzureUser.objects.filter(upn=upn).first()
        return self._result(user) if user else None

    def create_user(self, first_name, last_name, upn, initial_password, force_change_password=True):
        match = re.fullmatch(r'a([0-9]+)@eco\.unrc\.edu\.ar', upn)
        if match is None:
            raise ValueError('UPN de prueba inválido.')
        user, created = MockAzureUser.objects.get_or_create(upn=upn, defaults={
            'first_name': first_name, 'last_name': last_name, 'document_number': match[1],
            'password_profile_force_change': force_change_password,
        })
        return self._result(user, created=created)

    def assign_license(self, upn, sku_id):
        user = MockAzureUser.objects.get(upn=upn)
        user.assigned_sku = sku_id
        user.save(update_fields=['assigned_sku'])
        return LicenseResult(upn, sku_id, True)

    def set_temporary_password(self, user_identifier, password):
        # Solo confirma la mutación; el secreto no se persiste.
        user = MockAzureUser.objects.get(upn=user_identifier)
        user.password_profile_force_change = True
        user.save(update_fields=['password_profile_force_change'])
        return PasswordSetResult(user_identifier)


class MockMoodleClient:
    def __init__(self):
        require_local_mocks()

    def create_user(self, username, first_name, last_name, email, auth='oidc'):
        user, created = MockMoodleUser.objects.get_or_create(username=username, defaults={
            'first_name': first_name, 'last_name': last_name, 'email': email, 'auth_plugin': auth,
        })
        return MoodleUserResult(user.pk, user.username, user.email, created)

    def get_course_by_shortname(self, shortname):
        return MockMoodleCourse.objects.filter(shortname=shortname).values('id', 'shortname').first()

    def enrol_user(self, user_id, course_id, role_id=5):
        MockMoodleEnrollment.objects.get_or_create(user_id=user_id, course_id=course_id, defaults={'role_id': role_id})
        return MoodleEnrollmentResult(user_id, course_id, role_id, True)

    def get_or_create_group(self, course_id, group_name):
        if not MockMoodleCourse.objects.filter(pk=course_id).exists():
            raise ResourceNotFoundError('Curso mock inexistente.')
        # El modelo mock conserva el grupo en la matrícula, sin tabla de grupos.
        return course_id, group_name

    def add_user_to_group(self, group_id, user_id, *, course_id):
        group_course, group_name = group_id
        if group_course != course_id:
            raise ValueError('El grupo no pertenece al curso solicitado.')
        enrollment = MockMoodleEnrollment.objects.get(user_id=user_id, course_id=course_id)
        enrollment.group_name = group_name
        enrollment.save(update_fields=['group_name'])
        return MoodleGroupResult(course_id, course_id, user_id, 'add', True)


class MockMailerClient:
    def __init__(self):
        require_local_mocks()
        self.deliveries = []

    def send_credentials_email(self, recipient, full_name, upn, password):
        self.deliveries.append((recipient, upn))
        return MailDeliveryResult(recipient, 'Credenciales institucionales (mock)', True)
