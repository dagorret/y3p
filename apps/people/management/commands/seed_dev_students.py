"""Semilla determinista de alumnos y proveedores simulados, sin llamadas remotas.

La transacción integra identidad, vinculación, matrícula y evidencia Outbox.
Las claves estables permiten repetir la semilla sin duplicar registros; los
estados terminales del Outbox evitan despachar operaciones de prueba. Los
catálogos y membresías se siembran también en los mocks para que la sincronización
posterior preserve canales, invitados y el equipo de auditoría con un solo miembro.
"""

from collections import Counter
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.people.models import Affiliation, Identity, OutboxEvent, PersonalProfile, ProfileTypeDefinition, SagaStatusDefinition


class Command(BaseCommand):
    help = 'Crea alumnos de desarrollo y sincroniza sus plataformas simuladas de forma idempotente.'

    def add_arguments(self, parser):
        parser.add_argument('--count', type=int, default=60, help='Cantidad de alumnos de prueba (predeterminado: 60).')

    @transaction.atomic
    def handle(self, *args, **options):
        #FSEED01-8001: La semilla requiere habilitación explícita de plataformas simuladas.
        if not settings.ENABLE_LOCAL_MOCKS or not apps.is_installed('apps.platform_mocks'):
            raise CommandError('Habilite ENABLE_LOCAL_MOCKS para sembrar alumnos de desarrollo.')
        #FSEED02-8002: Rechaza cantidades inválidas antes de modificar datos.
        if not 1 <= options['count'] <= 10000:
            raise CommandError('La cantidad debe estar entre 1 y 10000.')
        from apps.platform_mocks import DEV_A1_SKU
        #FSEED03-8003: El SKU ficticio solo representa licencias en las plataformas simuladas.
        sku = settings.LUCY_M365_SKU_ID or DEV_A1_SKU
        from apps.platform_mocks.models import MockAzureUser, MockMoodleCourse, MockMoodleEnrollment, MockMoodleUser
        profile = ProfileTypeDefinition.objects.get(code='ALUMNO')
        states = list(SagaStatusDefinition.objects.filter(profile_type=profile, code__in=['PRE', 'ASP', 'INGRE', 'ALUMNO']).order_by('code'))
        #FSEED04-8004: El metamodelo debe estar migrado para generar vinculaciones válidas.
        if not states:
            raise CommandError('Ejecute las migraciones del metamodelo antes de sembrar.')
        course_names = ((9040, 'Introducción a la Economía'), (9041, 'Contabilidad I'), (9042, 'Administración General'))
        courses = [MockMoodleCourse.objects.update_or_create(pk=course_id, defaults={
            'shortname': str(course_id), 'fullname': name})[0] for course_id, name in course_names]
        students = []
        now = timezone.now()
        totals = Counter()
        for index in range(options['count']):
            bucket = index % 10
            color = 'Verde' if bucket < 5 else 'Amarillo' if bucket < 7 else 'Naranja' if bucket < 9 else 'Rojo'
            totals[color] += 1
            dni = str(95000000 + index)
            upn = f'a{dni}@eco.unrc.edu.ar'
            identity, _ = Identity.objects.update_or_create(identifier=dni, defaults={
                'category': 'FISICA', 'first_name': f'Estudiante {index + 1:04}', 'last_name': 'Desarrollo',
                'institutional_upn': upn, 'metadata': {'seed': 'seed_dev_students'},
            })
            PersonalProfile.objects.update_or_create(identity=identity, defaults={'personal_email': f'dev{dni}@example.test'})
            affiliation, _ = Affiliation.objects.update_or_create(identity=identity, profile_type=profile,
                defaults={'reference': ('2', '3', '7')[index % 3], 'current_status': states[index % len(states)],
                          'modality': 'PRESENCIAL' if index % 2 else 'DISTANCIA', 'is_active': True,
                          'expires_at': now + timedelta(days=-1 if bucket == 7 else 90)})
            azure, _ = MockAzureUser.objects.update_or_create(upn=upn, defaults={
                'first_name': identity.first_name, 'last_name': identity.last_name, 'document_number': dni,
                'is_active': True, 'assigned_sku': None if bucket == 8 else sku,
                'password_profile_force_change': bucket == 5, 'last_activity_at': None if bucket == 6 else now,
            })
            moodle, _ = MockMoodleUser.objects.update_or_create(username=upn, defaults={
                'email': upn, 'first_name': identity.first_name, 'last_name': identity.last_name, 'suspended': False})
            course = courses[index % len(courses)]
            MockMoodleEnrollment.objects.filter(user=moodle, course__in=courses).exclude(course=course).delete()
            MockMoodleEnrollment.objects.update_or_create(user=moodle, course=course, defaults={'role_id': 5, 'group_name': 'DESARROLLO'})
            OutboxEvent.objects.update_or_create(identity=identity, payload={'seed': 'seed_dev_students'}, defaults={
                'affiliation': affiliation, 'action': 'PROVISION', 'status': 'FAILED' if color == 'Rojo' else 'SUCCEEDED',
                'step_results': {}, 'last_error': 'Fallo simulado de desarrollo.' if color == 'Rojo' else '',
                'processed_at': now,
            })
            students.append((identity, azure))
        teams, channels, members = self.seed_catalogs(students, courses, now)
        self.stdout.write(self.style.SUCCESS(
            f"Semilla sincronizada: {options['count']} alumnos. " + ', '.join(f'{color}: {totals[color]}' for color in ('Verde', 'Amarillo', 'Naranja', 'Rojo')) + '.'))
        self.stdout.write(self.style.SUCCESS(
            f'Espejos sembrados: {teams} equipos, {channels} canales, {members} miembros y {len(courses)} cursos.'))

    def seed_catalogs(self, students, courses, now):
        """Concilia tres catálogos estables y su pivot sin consultar servicios remotos.

        Los UUID y los identificadores de canal son deterministas. Las tablas mock
        son el origen de la lectura del espejo; el mismo servicio de conciliación
        recalcula contadores y elimina membresías de pruebas ya retiradas.
        """
        from apps.platform_mocks.models import MockAzureUser, MockTeam, MockTeamChannel, MockTeamMember, MockMoodleCourse, MockMoodleEnrollment
        from apps.platform_mocks.clients import MockGraphClient
        from apps.integrations.catalogs import _complete_teams, _save_teams
        from apps.integrations.models import TeamWorkspace, TeamsWorkspaceMirror, TeamsChannelMirror, TeamsMemberMirror, MoodleCourseMirror
        #FSEED05-8005: Migra solo matrículas de esta semilla y retira el catálogo anterior si quedó vacío.
        legacy = MockMoodleCourse.objects.filter(shortname='DEV-ALUMNOS').first()
        if legacy:
            MockMoodleEnrollment.objects.filter(course=legacy, user__username__in=[azure.upn for _, azure in students]).delete()
            if not legacy.enrollments.exists():
                MoodleCourseMirror.objects.filter(course_id=legacy.pk, shortname='DEV-ALUMNOS').delete()
                legacy.delete()
        for course in courses:
            MoodleCourseMirror.objects.update_or_create(course_id=course.pk, defaults={
                'shortname': course.shortname, 'fullname': course.fullname, 'category_name': 'Desarrollo',
                'visible': True, 'enrolled_count': course.enrollments.filter(user__suspended=False).count(), 'synced_at': now})
        guest, _ = MockAzureUser.objects.update_or_create(upn='invitado.desarrollo@example.test', defaults={
            'first_name': 'Invitado', 'last_name': 'Externo', 'is_active': True, 'assigned_sku': None,
            'password_profile_force_change': False, 'last_activity_at': now})
        teams = []
        memberships = [[], [], [students[-1][1]]]
        for index, (_, azure) in enumerate(students[:-1]):
            memberships[index % 2].append(azure)
        for index, course in enumerate(courses):
            assigned = memberships[index]
            owner = assigned[0] if assigned else students[0][1]
            team_id = uuid5(NAMESPACE_URL, f'y3p/seed_dev_students/teams/{course.pk}')
            team, _ = MockTeam.objects.update_or_create(pk=team_id, defaults={
                'display_name': f'Desarrollo · {course.fullname}', 'description': 'Equipo de desarrollo reproducible.', 'owner': owner})
            teams.append(team)
            TeamWorkspace.objects.update_or_create(pk=team_id, defaults={'display_name': team.display_name,
                'owner_upn': owner.upn, 'moodle_shortname': course.shortname, 'career_code': ('2', '3', '7')[index], 'commission': 'COMISIÓN 1'})
            desired = assigned + ([guest] if index == 0 else [])
            team.members.exclude(user_id__in=[azure.pk for azure in desired]).delete()
            for azure in desired:
                #FSEED06-8006: El tercer equipo conserva un solo miembro sin propietarios en la pivot para auditoría.
                MockTeamMember.objects.update_or_create(team=team, user=azure,
                    defaults={'is_owner': index != 2 and azure.pk == owner.pk})
            channel_ids = []
            for suffix, name, membership_type in (('general', 'General', 'standard'), ('comision-1', 'Comisión 1', 'private')):
                channel_id = f'{team_id}:{suffix}'
                channel_ids.append(channel_id)
                MockTeamChannel.objects.update_or_create(channel_id=channel_id, defaults={
                    'team': team, 'display_name': name, 'membership_type': membership_type})
            team.channels.exclude(channel_id__in=channel_ids).delete()
        graph = MockGraphClient()
        _save_teams(_complete_teams(graph, [graph.get_team(str(team.pk)) for team in teams]), now)
        mirrors = TeamsWorkspaceMirror.objects.filter(team_id__in=[str(team.pk) for team in teams])
        return (mirrors.count(), TeamsChannelMirror.objects.filter(workspace__in=mirrors).count(),
                TeamsMemberMirror.objects.filter(workspace__in=mirrors).count())
