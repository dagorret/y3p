"""Clientes locales persistentes para pruebas; nunca usan transportes de red.

Se inyectan explícitamente en el orquestador. ENABLE_LOCAL_MOCKS no redirige
los clientes reales ni implica dry-run: estas escrituras son en tablas mock.
"""

import re
from django.db import transaction

from apps.integrations.dataclasses import (
    ExternalActionResult, GraphUserResult, LicenseResult, MailDeliveryResult, MoodleEnrollmentResult,
    CourseParticipant, SialStudentDTO, TeamMemberResult,
    MoodleGroupResult, MoodleUserResult, PasswordSetResult,
)
from apps.integrations.exceptions import ResourceNotFoundError
from .models import MockAzureUser, MockMoodleCourse, MockMoodleEnrollment, MockMoodleUser, MockTeam, MockTeamMember
from .services import require_local_mocks


class MockGraphClient:
    dry_run = False
    def __init__(self):
        require_local_mocks()

    def _result(self, user, *, created=False):
        return GraphUserResult(str(user.pk), user.upn, f'{user.first_name} {user.last_name}', created)

    def get_user(self, upn):
        user = MockAzureUser.objects.filter(upn=upn).first()
        return self._result(user) if user else None

    def create_user(self, first_name, last_name, upn, initial_password, force_change_password=True):
        match = re.fullmatch(r'a([0-9]+)@eco\.unrc\.edu\.ar', upn)
        if not upn.endswith('@eco.unrc.edu.ar') or not upn.split('@')[0]:
            #FPLATFORMMOCKSCLIENTS001-8001: Origina ValueError; el llamador conserva la causa y decide su tratamiento.
            raise ValueError('UPN de prueba inválido.')
        user, created = MockAzureUser.objects.get_or_create(upn=upn, defaults={
            'first_name': first_name, 'last_name': last_name, 'document_number': match[1] if match else '',
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

    def reset_password_temporary(self, user_id_or_upn, temp_password):
        self.set_temporary_password(user_id_or_upn, temp_password)
        return ExternalActionResult('reset_password')

    def get_user_license_ids(self, upn):
        user = MockAzureUser.objects.get(upn=upn)
        return (user.assigned_sku,) if user.assigned_sku else ()

    def toggle_license(self, upn, sku_id, assign):
        user = MockAzureUser.objects.get(upn=upn)
        user.assigned_sku = sku_id if assign else None
        user.save(update_fields=['assigned_sku'])
        return ExternalActionResult('assign_license' if assign else 'remove_license')

    @transaction.atomic
    def create_team_from_template(self, display_name, description, owner_upn):
        owner = MockAzureUser.objects.filter(upn=owner_upn).first()
        if owner is None:
            #FPLATFORMMOCKSCLIENTS002-8002: Origina ResourceNotFoundError; el llamador conserva la causa y decide su tratamiento.
            raise ResourceNotFoundError('No existe el propietario en Azure mock.')
        team = MockTeam.objects.create(display_name=display_name, description=description, owner=owner)
        MockTeamMember.objects.create(team=team, user=owner, is_owner=True)
        return ExternalActionResult('create_team', resource_id=str(team.pk))

    @transaction.atomic
    def purge_team_members(self, team_id, keep_owners=True):
        team = MockTeam.objects.filter(pk=team_id).first()
        if team is None:
            #FPLATFORMMOCKSCLIENTS003-8003: Origina ResourceNotFoundError; el llamador conserva la causa y decide su tratamiento.
            raise ResourceNotFoundError('No existe el equipo mock.')
        members = team.members.all()
        if keep_owners:
            members = members.filter(is_owner=False)
        count = members.count()
        members.delete()
        return ExternalActionResult('purge_team', resource_id=str(team.pk), affected_count=count)

    def get_team_members(self, team_id):
        if not MockTeam.objects.filter(pk=team_id).exists():
            #FPLATFORMMOCKSCLIENTS004-8004: Origina ResourceNotFoundError; el llamador conserva la causa y decide su tratamiento.
            raise ResourceNotFoundError('No existe el equipo mock.')
        return tuple(TeamMemberResult(str(member.pk), str(member.user_id), member.user.upn,
            f'{member.user.first_name} {member.user.last_name}'.strip(), member.is_owner)
            for member in MockTeamMember.objects.filter(team_id=team_id).select_related('user'))

    def list_teams(self):
        return [self.get_team(str(team.pk)) for team in MockTeam.objects.all()]

    def get_team(self, team_id):
        team = MockTeam.objects.get(pk=team_id)
        return {'id': str(team.pk), 'displayName': team.display_name, 'description': team.description, 'mailNickname': ''}

    def get_team_channels(self, team_id):
        from .models import MockTeamChannel
        channels = list(MockTeamChannel.objects.filter(team_id=team_id).order_by('channel_id'))
        if channels:
            return [{'id': channel.channel_id, 'displayName': channel.display_name, 'membershipType': channel.membership_type}
                    for channel in channels]
        return [{'id': f'{team_id}:general', 'displayName': 'General', 'membershipType': 'standard'}]

    def get_user_joined_teams(self, user_id):
        return [str(pk) for pk in MockTeamMember.objects.filter(user_id=user_id).values_list('team_id', flat=True)]

    def get_user_state(self, upn):
        user = MockAzureUser.objects.get(upn=upn)
        return {'accountEnabled': user.is_active, 'passwordProfile': {'forceChangePasswordNextSignIn': user.password_profile_force_change}}

    def add_team_member(self, team_id, user_id_or_upn):
        user = MockAzureUser.objects.filter(upn=user_id_or_upn, is_active=True).first()
        if user is None:
            #FPLATFORMMOCKSCLIENTS005-8005: Origina ResourceNotFoundError; el llamador conserva la causa y decide su tratamiento.
            raise ResourceNotFoundError('No existe una cuenta Azure activa para ese miembro.')
        _, created = MockTeamMember.objects.get_or_create(team_id=team_id, user=user)
        return ExternalActionResult('add_team_member', affected_count=int(created))


class MockMoodleClient:
    dry_run = False
    def __init__(self):
        require_local_mocks()

    def create_user(self, username, first_name, last_name, email, auth='oidc'):
        user, created = MockMoodleUser.objects.get_or_create(username=username, defaults={
            'first_name': first_name, 'last_name': last_name, 'email': email, 'auth_plugin': auth,
        })
        return MoodleUserResult(user.pk, user.username, user.email, created)

    def get_user_by_username(self, username):
        user = MockMoodleUser.objects.filter(username=username).first()
        return MoodleUserResult(user.pk, user.username, user.email, False) if user else None

    def get_course_by_shortname(self, shortname):
        return MockMoodleCourse.objects.filter(shortname=shortname).values('id', 'shortname').first()

    def get_course_participants(self, course_id):
        return tuple(CourseParticipant(enrollment.user_id, enrollment.user.username,
            enrollment.user.first_name, enrollment.user.last_name, (enrollment.role_id,),
            (enrollment.group_name,) if enrollment.group_name else ())
            for enrollment in MockMoodleEnrollment.objects.filter(course_id=course_id, user__suspended=False).select_related('user'))

    def list_courses(self):
        return [{'id': course.pk, 'shortname': course.shortname, 'fullname': course.fullname,
                 'category_name': 'Desarrollo', 'visible': True} for course in MockMoodleCourse.objects.all()]

    def get_user_courses(self, user_id):
        return [{'id': enrollment.course_id, 'shortname': enrollment.course.shortname}
                for enrollment in MockMoodleEnrollment.objects.filter(user_id=user_id, user__suspended=False).select_related('course')]

    def enrol_user(self, user_id, course_id, role_id=5):
        MockMoodleEnrollment.objects.get_or_create(user_id=user_id, course_id=course_id, defaults={'role_id': role_id})
        return MoodleEnrollmentResult(user_id, course_id, role_id, True)

    def get_or_create_group(self, course_id, group_name):
        if not MockMoodleCourse.objects.filter(pk=course_id).exists():
            #FPLATFORMMOCKSCLIENTS006-8006: Origina ResourceNotFoundError; el llamador conserva la causa y decide su tratamiento.
            raise ResourceNotFoundError('Curso mock inexistente.')
        # El modelo mock conserva el grupo en la matrícula, sin tabla de grupos.
        return course_id, group_name

    def add_user_to_group(self, group_id, user_id, *, course_id):
        group_course, group_name = group_id
        if group_course != course_id:
            #FPLATFORMMOCKSCLIENTS007-8007: Origina ValueError; el llamador conserva la causa y decide su tratamiento.
            raise ValueError('El grupo no pertenece al curso solicitado.')
        enrollment = MockMoodleEnrollment.objects.get(user_id=user_id, course_id=course_id)
        enrollment.group_name = group_name
        enrollment.save(update_fields=['group_name'])
        return MoodleGroupResult(course_id, course_id, user_id, 'add', True)


class MockMailerClient:
    dry_run = False
    def __init__(self):
        require_local_mocks()
        self.deliveries = []

    def send_email(self, recipient, subject, text_body, html_body=None):
        self.deliveries.append((recipient, subject, text_body))
        return MailDeliveryResult(recipient, subject, True)

    def send_credentials_email(self, recipient, full_name, upn, password):
        self.deliveries.append((recipient, upn))
        return MailDeliveryResult(recipient, 'Credenciales institucionales (mock)', True)


class MockSialClient:
    def get_student_lists(self, campaign_id):
        require_local_mocks()
        from .models import MockSialRecord
        conditions = {'preinscriptos': 'PREINSCRIPTO', 'aspirantes': 'ASPIRANTE', 'ingresantes': 'INGRESANTE'}
        return {name: [SialStudentDTO(record.dni, record.first_name, record.last_name, record.personal_email,
            record.career_code, record.modality, record.condition, record.commission or '')
            for record in MockSialRecord.objects.filter(condition=condition)] for name, condition in conditions.items()}
