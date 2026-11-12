"""Aislamiento académico, promoción atómica, barrido TTL y observabilidad del Outbox."""

from datetime import timedelta
from io import BytesIO, StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db.models import Count
from django.template.loader import render_to_string
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from openpyxl import load_workbook

from apps.people.models import Affiliation, Identity, PersonalProfile, ProfileTypeDefinition, SagaStatusDefinition, OutboxEvent, AffiliationStatusHistory
from apps.people.scopes import SCOPES, scope_url, has_scope_permission
from apps.portal.academic_services import confirm_preinscriptions
from apps.portal.student_grid import student_grid_context
from apps.portal.outbox_monitor import rearm_failed_events


@override_settings(ENABLE_LOCAL_MOCKS=True, LUCY_DRY_RUN=False, LUCY_M365_SKU_ID='scope-a1')
class AcademicOutboxTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.platform_mocks.models import MockMoodleCourse
        from apps.integrations.models import MoodleCourseMirror
        cls.users, cls.students, cls.affiliations = {}, {}, {}
        for scope, (_, _, _) in SCOPES.items():
            group_prefix = 'alumnos' if scope == 'GRADO' else scope.lower()
            cls.users[scope] = {}
            for role in ('jefe', 'operador', 'admin'):
                user = get_user_model().objects.create_user(f'{scope}-{role}')
                user.groups.add(Group.objects.get(name=f'adm_{group_prefix}_rol_{role}'))
                cls.users[scope][role] = user
            profile = ProfileTypeDefinition.objects.get(code=SCOPES[scope][0])
            pre = SagaStatusDefinition.objects.get(profile_type=profile, code='PRE')
            confirmed = SagaStatusDefinition.objects.get(profile_type=profile, code='ALUMNO')
            prefix = {'GRADO': 71000000, 'POSGRADO': 72000000, 'EXTENSION': 73000000}[scope]
            people = Identity.objects.bulk_create([Identity(category='FISICA', identifier=str(prefix + index),
                first_name=f'Persona {index:03}', last_name=scope, institutional_upn=f'a{prefix + index}@eco.unrc.edu.ar') for index in range(53)])
            PersonalProfile.objects.bulk_create([PersonalProfile(identity=person, personal_email=f'{person.identifier}@example.test') for person in people])
            reference = {'GRADO': '2', 'POSGRADO': '9040', 'EXTENSION': '9042'}[scope]
            affiliations = Affiliation.objects.bulk_create([Affiliation(identity=person, profile_type=profile,
                current_status=pre if index % 2 == 0 else confirmed, reference=reference,
                expires_at=timezone.now() + timedelta(days=30)) for index, person in enumerate(people)])
            cls.students[scope], cls.affiliations[scope] = people, affiliations
        for course_id in (9040, 9042):
            MockMoodleCourse.objects.create(pk=course_id, shortname=str(course_id), fullname=f'Programa {course_id}')
            MoodleCourseMirror.objects.create(course_id=course_id, shortname=str(course_id), fullname=f'Programa {course_id}', synced_at=timezone.now())
        cls.superuser = get_user_model().objects.create_superuser('institucional')

    def setUp(self):
        self.enterContext(patch('apps.people.services.current_app.send_task'))
        self.client.force_login(self.users['POSGRADO']['operador'])

    def post_bulk(self, scope, action='confirm', indices=(0, 1, 2), **data):
        return self.client.post(scope_url(scope, 'bulk'), {'action': action,
            'identity_ids': [self.students[scope][index].pk for index in indices], **data}, HTTP_HX_REQUEST='true')

    def event(self, scope, index=0, status='FAILED', action='PROVISION'):
        return OutboxEvent.objects.create(identity=self.students[scope][index], affiliation=self.affiliations[scope][index],
            action=action, status=status, attempts=3, last_error='Fallo de prueba')

    def test_scope_authorization_matrix_grid_export_builder_and_registration(self):
        for owner in SCOPES:
            self.client.force_login(self.users[owner]['operador'])
            for target in SCOPES:
                expected = 200 if owner == target else 403
                for operation in ('grid', 'export', 'builder'):
                    response = self.client.get(scope_url(target, operation))
                    self.assertEqual(response.status_code, expected, (owner, target, operation))
                    if getattr(response, 'streaming', False):
                        b''.join(response.streaming_content)
            response = self.client.get(scope_url(owner))
            self.assertEqual(response.context['page_obj'].paginator.count, 53)
            self.assertTrue(all(identity.last_name == owner for identity in response.context['identities']))
        self.client.force_login(self.users['POSGRADO']['operador'])
        self.assertEqual(self.client.get(reverse('portal:extension_registration')).status_code, 403)
        self.assertEqual(self.client.post(reverse('portal:extension_registration'), {}).status_code, 403)

    def test_cross_scope_identity_and_post_batches_fail_403_without_events(self):
        for scope in ('POSGRADO', 'EXTENSION'):
            self.client.force_login(self.users[scope]['operador'])
            other = 'EXTENSION' if scope == 'POSGRADO' else 'POSGRADO'
            self.assertEqual(self.client.get(scope_url(scope, 'detail', identity_id=self.students[other][0].pk)).status_code, 403)
            for action in ('confirm', 'notification'):
                response = self.client.post(scope_url(scope, 'bulk'), {'action': action,
                    'identity_ids': [self.students[scope][0].pk, self.students[other][0].pk], 'subject': 'Aviso', 'body': 'Hola {full_name}'})
                self.assertEqual(response.status_code, 403)
            self.assertEqual(self.client.get(scope_url(scope), {'scope': other}).status_code, 403)
        self.assertFalse(OutboxEvent.objects.exists())
        self.client.force_login(self.users['GRADO']['operador'])
        self.assertEqual(self.client.get(scope_url('GRADO', 'detail', identity_id=self.students['POSGRADO'][0].pk)).status_code, 403)
        self.assertEqual(self.client.post(scope_url('GRADO', 'bulk'), {'action': 'notification', 'identity_ids': [self.students['EXTENSION'][0].pk], 'subject': 'Aviso', 'body': 'Mensaje'}).status_code, 403)

    def test_scoped_grid_uses_seven_queries_zero_render_queries_and_preserves_filters(self):
        for scope in ('POSGRADO', 'EXTENSION'):
            user = self.users[scope]['operador']
            has_scope_permission(user, scope)
            for query in ('', self.students[scope][0].identifier):
                with self.assertNumQueries(7):
                    context = student_grid_context({'q': query, 'page_size': '25'}, user=user, scope=scope)
                with self.assertNumQueries(0):
                    render_to_string('portal/fragments/administracion_results.html', context)
            self.client.force_login(user)
            response = self.client.get(scope_url(scope), {'status': 'PRE', 'health': 'yellow', 'page_size': '25'}, HTTP_HX_REQUEST='true')
            self.assertEqual(response.context['page_obj'].paginator.count, 27)
            self.assertIn('page_size=25', response.context['next_url'])
            self.assertIn('status=PRE', response.context['next_url'])
            self.assertTrue(response.context['next_url'].startswith(scope_url(scope)))
            self.assertContains(response, 'Confirmar Preinscriptos')
            self.assertContains(response, 'hx-push-url="true"')
            self.assertNotContains(response, '<html')

    def test_scoped_export_matches_filters_and_ignores_page_boundary(self):
        for scope in ('POSGRADO', 'EXTENSION'):
            self.client.force_login(self.users[scope]['operador'])
            response = self.client.get(scope_url(scope, 'export'), {'status': 'PRE', 'page_size': 25, 'page': 2})
            self.assertEqual(response['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            rows = list(load_workbook(BytesIO(b''.join(response.streaming_content))).active.values)
            self.assertEqual(len(rows), 28)
            self.assertTrue(all(row[5] == 'PRE' and row[2].startswith(scope) for row in rows[1:]))
            self.assertTrue(all(scope_url(scope) in row[8] for row in rows[1:]))
            self.client.force_login(self.users['GRADO']['admin'])
            self.assertEqual(self.client.get(scope_url(scope, 'export')).status_code, 403)

    def test_confirmation_is_idempotent_traced_and_creates_only_one_provision_per_pre(self):
        for scope in ('POSGRADO', 'EXTENSION'):
            self.client.force_login(self.users[scope]['operador'])
            self.assertContains(self.post_bulk(scope), 'Confirmados: 2. Omitidos: 1.')
            self.assertContains(self.post_bulk(scope), 'Confirmados: 0. Omitidos: 3.')
            events = OutboxEvent.objects.filter(affiliation__profile_type__code=scope)
            self.assertEqual(events.count(), 2)
            for event in events:
                self.assertEqual(event.action, 'PROVISION')
                self.assertTrue(event.payload['send_welcome'])
                self.assertEqual(event.requested_by_id, self.users[scope]['operador'].pk)
                self.assertEqual(event.payload['profile_type_code'], scope)
                self.assertNotIn('password', event.payload)
            history = AffiliationStatusHistory.objects.filter(affiliation__profile_type__code=scope)
            self.assertEqual(history.count(), 2)
            self.assertTrue(all(row.from_status.code == 'PRE' and row.to_status.code == 'ALUMNO' for row in history))

    def test_confirmation_atomic_rollback_when_second_outbox_write_fails(self):
        from apps.people.services import _emit_outbox_event
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('Fallo controlado del segundo evento')
            return _emit_outbox_event(*args, **kwargs)
        with patch('apps.people.services._emit_outbox_event', side_effect=fail_second), self.assertRaises(RuntimeError):
            confirm_preinscriptions([self.students['POSGRADO'][index].pk for index in (0, 2)],
                user=self.users['POSGRADO']['operador'], scope='POSGRADO')
        self.assertFalse(OutboxEvent.objects.exists())
        self.assertFalse(AffiliationStatusHistory.objects.exists())
        self.assertEqual(Affiliation.objects.filter(pk__in=[self.affiliations['POSGRADO'][i].pk for i in (0, 2)], current_status__code='PRE').count(), 2)

    def test_confirmation_worker_delivers_welcome_credentials_and_enrols_moodle(self):
        from apps.platform_mocks.clients import MockGraphClient, MockMoodleClient, MockMailerClient
        from apps.provisioning.dispatcher import claim_events
        from apps.provisioning.orchestrator import run_saga, run_moodle_step
        SagaStatusDefinition.objects.filter(profile_type__code='POSGRADO', code='ALUMNO').update(requires_collaboration_license=True)
        self.post_bulk('POSGRADO', indices=(0,))
        event_id = claim_events()[0]
        graph, moodle, mailer = MockGraphClient(), MockMoodleClient(), MockMailerClient()
        self.assertEqual(run_saga(event_id, graph=graph, moodle=moodle, mailer=mailer, dry_run=False, enqueue_moodle=lambda pk: None), 'WAITING_SUBTASK')
        self.assertEqual(run_moodle_step(event_id, moodle=moodle, dry_run=False), 'PENDING')
        self.assertEqual(claim_events(), [event_id])
        self.assertEqual(run_saga(event_id, graph=graph, moodle=moodle, mailer=mailer, dry_run=False), 'SUCCEEDED')
        self.assertEqual(len(mailer.deliveries), 1)
        self.assertEqual(graph.get_user_license_ids(self.students['POSGRADO'][0].institutional_upn), ('scope-a1',))
        self.assertEqual(run_saga(event_id, graph=graph, moodle=moodle, mailer=mailer, dry_run=False), 'SUCCEEDED')
        self.assertEqual(len(mailer.deliveries), 1)

    def test_scoped_email_notification_substitution_and_worker_permission_revocation(self):
        from apps.provisioning.dispatcher import claim_events
        from apps.provisioning.orchestrator import run_saga
        response = self.post_bulk('POSGRADO', action='notification', indices=(0,), subject='Aviso {document_number}', body='Hola {full_name}: {upn}')
        self.assertContains(response, '1 solicitudes registradas')
        event = OutboxEvent.objects.get()
        self.assertEqual(event.payload['scope'], 'POSGRADO')
        self.assertIn(self.students['POSGRADO'][0].institutional_upn, event.payload['support_options']['body'])
        self.users['POSGRADO']['operador'].groups.clear()
        event_id = claim_events()[0]
        with patch('apps.people.support.build_mailer') as mailer:
            self.assertEqual(run_saga(event_id, dry_run=False), 'FAILED')
            mailer.assert_not_called()

    def test_read_only_and_csrf_cannot_confirm_preinscriptions(self):
        self.client.force_login(self.users['POSGRADO']['jefe'])
        self.assertEqual(self.post_bulk('POSGRADO').status_code, 403)
        self.assertNotContains(self.client.get(scope_url('POSGRADO')), 'student-bulk-form')
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.users['POSGRADO']['operador'])
        self.assertEqual(client.post(scope_url('POSGRADO', 'bulk'), {'action': 'confirm', 'identity_ids': [self.students['POSGRADO'][0].pk]}).status_code, 403)

    def test_shared_identity_ficha_only_displays_current_scope_affiliations(self):
        source = self.students['POSGRADO'][0]
        affiliation = self.affiliations['EXTENSION'][0]
        affiliation.identity = source
        affiliation.reference = 'REFERENCIA-EXTENSION-SECRETA'
        affiliation.save()
        response = self.client.get(scope_url('POSGRADO', 'detail', identity_id=source.pk))
        self.assertContains(response, '9040')
        self.assertNotContains(response, 'REFERENCIA-EXTENSION-SECRETA')

    def test_snippets_are_isolated_by_scope(self):
        from apps.portal.snippet_services import create_snippet, visible_snippets
        create_snippet(user=self.users['POSGRADO']['operador'], name='Solo posgrado', expression='saga:PRE', filter_params={}, is_shared=True, scope='POSGRADO')
        self.assertEqual([snippet.name for snippet in visible_snippets(self.superuser, scope='POSGRADO')], ['Solo posgrado'])
        self.assertEqual(visible_snippets(self.superuser, scope='EXTENSION'), [])
        self.assertEqual(visible_snippets(self.superuser, scope='GRADO'), [])

    def test_ttl_periodic_task_is_bounded_idempotent_and_skips_orphans(self):
        from apps.people.tasks import process_expired_affiliations
        expired = [self.affiliations['POSGRADO'][0], self.affiliations['EXTENSION'][0]]
        Affiliation.objects.filter(pk__in=[aff.pk for aff in expired] + [self.affiliations['GRADO'][1].pk]).update(expires_at=timezone.now() - timedelta(seconds=1))
        first = process_expired_affiliations.run(limit=1)
        self.assertEqual((first['expired'], first['orphans']), (1, 1))
        self.assertEqual(process_expired_affiliations.run(limit=1)['expired'], 1)
        self.assertEqual(process_expired_affiliations.run(limit=1)['expired'], 0)
        self.assertEqual(OutboxEvent.objects.filter(action='SUSPEND').count(), 2)
        self.assertEqual(AffiliationStatusHistory.objects.filter(rule__is_expiration=True, changed_by='tarea:ttl_periodico').count(), 2)
        for aff in expired:
            aff.refresh_from_db()
            self.assertFalse(aff.is_active)
        beat = settings.CELERY_BEAT_SCHEDULE['affiliations-expire-every-fifteen-minutes']
        self.assertEqual(beat['task'], 'people.process_expired_affiliations')
        self.assertEqual(beat['schedule'].minute, {0, 15, 30, 45})
        self.assertEqual(beat['kwargs'], {'limit': 500})

    def test_grade_expiration_also_emits_suspend_and_unprovisioned_account_is_safe(self):
        from apps.people.tasks import process_expired_affiliations
        from apps.platform_mocks.clients import MockGraphClient, MockMoodleClient, MockMailerClient
        from apps.provisioning.dispatcher import claim_events
        from apps.provisioning.orchestrator import run_saga, run_moodle_step
        Affiliation.objects.filter(pk=self.affiliations['GRADO'][0].pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(process_expired_affiliations.run()['expired'], 1)
        event_id = claim_events()[0]
        clients = {'graph': MockGraphClient(), 'moodle': MockMoodleClient(), 'mailer': MockMailerClient(), 'dry_run': False}
        self.assertEqual(run_saga(event_id, enqueue_moodle=lambda pk: None, **clients), 'WAITING_SUBTASK')
        self.assertEqual(run_moodle_step(event_id, moodle=clients['moodle'], dry_run=False), 'PENDING')
        claim_events()
        self.assertEqual(run_saga(event_id, **clients), 'SUCCEEDED')

    def test_monitor_counts_filters_and_badge_are_scoped_and_read_only(self):
        self.event('POSGRADO', status='PENDING')
        self.event('POSGRADO', index=1, status='FAILED')
        self.event('EXTENSION', status='FAILED')
        response = self.client.get(reverse('portal:outbox_monitor'), {'status': 'FAILED'})
        self.assertEqual(response.context['counts'], {'PENDING': 1, 'PROCESSING': 0, 'SUCCEEDED': 0, 'FAILED': 1})
        self.assertEqual(response.context['events_page'].paginator.count, 1)
        self.assertNotContains(response, self.students['EXTENSION'][0].identifier)
        self.assertNotContains(response, 'Rearmar seleccionados')
        #FPORTALPRUEBAS02-12302: Verifica el número visible sin acoplarse a los saltos de línea.
        badge = self.client.get(reverse('portal:outbox_badge'))
        self.assertEqual(badge.status_code, 200)
        self.assertEqual(strip_tags(badge.content.decode()).strip(), '2')
        self.assertEqual(OutboxEvent.objects.filter(status='FAILED').count(), 2)
        self.client.force_login(self.users['POSGRADO']['jefe'])
        self.assertEqual(self.client.get(reverse('portal:outbox_monitor')).status_code, 403)
        self.assertEqual(self.client.get(reverse('portal:outbox_badge')).status_code, 403)

    def test_monitor_rearm_is_admin_only_atomic_preserves_checkpoints_and_original_actor(self):
        event = self.event('POSGRADO')
        event.requested_by = self.users['POSGRADO']['operador']
        event.step_results = {'graph_user': {'status': 'CONFIRMED', 'details': {'created': True}}}
        event.save()
        url = reverse('portal:outbox_monitor')
        self.assertEqual(self.client.post(url, {'event_ids': [event.pk]}).status_code, 403)
        self.client.force_login(self.users['POSGRADO']['admin'])
        other = self.event('EXTENSION')
        self.assertEqual(self.client.post(url, {'event_ids': [event.pk, other.pk]}).status_code, 403)
        event.refresh_from_db()
        self.assertEqual(event.status, 'FAILED')
        self.assertContains(self.client.post(url, {'event_ids': [event.pk]}, HTTP_HX_REQUEST='true'), 'Eventos rearmados: 1.')
        event.refresh_from_db()
        self.assertEqual((event.status, event.attempts, event.last_error), ('PENDING', 0, ''))
        self.assertEqual(event.requested_by_id, self.users['POSGRADO']['operador'].pk)
        self.assertEqual(event.step_results['graph_user']['status'], 'CONFIRMED')
        self.assertTrue(LogEntry.objects.filter(user=self.users['POSGRADO']['admin'], object_id=str(event.pk), change_message__contains='rearme_outbox').exists())
        self.assertContains(self.client.post(url, {'event_ids': [event.pk]}), 'Eventos rearmados: 0. Omitidos: 1.')

    def test_monitor_pagination_invalid_filters_and_csrf(self):
        OutboxEvent.objects.bulk_create([OutboxEvent(identity=self.students['POSGRADO'][0], affiliation=self.affiliations['POSGRADO'][0], action='SUPPORT', status='FAILED') for _ in range(61)])
        response = self.client.get(reverse('portal:outbox_monitor'), {'status': 'FAILED', 'action': 'SUPPORT', 'page': 2})
        self.assertEqual(len(response.context['events_page']), 11)
        self.assertContains(response, 'status=FAILED&amp;action=SUPPORT')
        self.assertEqual(self.client.get(reverse('portal:outbox_monitor'), {'status': 'NO_VALIDO'}).status_code, 400)
        self.client.force_login(self.users['POSGRADO']['admin'])
        self.assertContains(self.client.post(reverse('portal:outbox_monitor'), {'status': 'INVALIDO', 'event_ids': [OutboxEvent.objects.first().pk]}), 'no son válidos')
        self.assertFalse(OutboxEvent.objects.filter(status='PENDING').exists())
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.users['POSGRADO']['admin'])
        self.assertEqual(client.post(reverse('portal:outbox_monitor'), {'event_ids': [OutboxEvent.objects.first().pk]}).status_code, 403)

    def test_seed_adds_both_scopes_and_is_idempotent(self):
        for _ in range(2):
            call_command('seed_dev_students', count=60, stdout=StringIO())
        for scope in ('POSGRADO', 'EXTENSION'):
            rows = Affiliation.objects.filter(identity__metadata__seed='seed_dev_academic_students', profile_type__code=scope)
            self.assertEqual(rows.count(), 20)
            self.assertEqual(dict(rows.values_list('current_status__code').annotate(total=Count('pk'))), {'PRE': 10, 'ALUMNO': 10})
            self.assertEqual(OutboxEvent.objects.filter(identity__metadata__seed='seed_dev_academic_students', affiliation__profile_type__code=scope).count(), 10)

    def test_shared_identity_grade_cannot_mutate_other_scope_ttl_or_suspend_all(self):
        identity = self.students['GRADO'][0]
        other = self.affiliations['POSGRADO'][0]
        other.identity = identity
        other.save()
        self.client.force_login(self.users['GRADO']['admin'])
        response = self.client.post(reverse('portal:affiliation_ttl', args=[identity.pk, other.pk]), {'expires_at': '2028-01-01', 'reason': 'Intento cruzado'})
        self.assertEqual(response.status_code, 403)
        with patch('apps.people.support.build_graph') as graph:
            self.assertEqual(self.client.post(reverse('portal:identity_suspend', args=[identity.pk])).status_code, 403)
            graph.assert_not_called()
        other.refresh_from_db()
        self.assertTrue(other.is_active)

    def test_ttl_preserves_moodle_when_another_scope_still_requires_same_course(self):
        from apps.people.ttl import process_expired_affiliations
        from apps.provisioning.dispatcher import claim_events
        from apps.provisioning.orchestrator import run_saga, run_moodle_step
        from apps.platform_mocks.clients import MockGraphClient, MockMoodleClient, MockMailerClient
        graph, moodle, mailer = MockGraphClient(), MockMoodleClient(), MockMailerClient()
        identity = self.students['POSGRADO'][0]
        remote = graph.create_user(identity.first_name, identity.last_name, identity.institutional_upn, 'Clave de prueba')
        graph.assign_license(identity.institutional_upn, 'scope-a1')
        user = moodle.create_user(identity.institutional_upn, identity.first_name, identity.last_name, identity.institutional_upn)
        moodle.enrol_user(user.id, 9040)
        shared = self.affiliations['EXTENSION'][1]
        shared.identity, shared.reference = identity, '9040'
        shared.save()
        shared.current_status.requires_collaboration_license = True
        shared.current_status.save()
        Affiliation.objects.filter(pk=self.affiliations['POSGRADO'][0].pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(process_expired_affiliations()['expired'], 1)
        event_id = claim_events()[0]
        self.assertEqual(run_saga(event_id, graph=graph, moodle=moodle, mailer=mailer, dry_run=False, enqueue_moodle=lambda pk: None), 'WAITING_SUBTASK')
        self.assertEqual(run_moodle_step(event_id, moodle=moodle, dry_run=False), 'PENDING')
        claim_events()
        self.assertEqual(run_saga(event_id, graph=graph, moodle=moodle, mailer=mailer, dry_run=False), 'SUCCEEDED')
        from apps.platform_mocks.models import MockAzureUser, MockMoodleEnrollment
        self.assertTrue(MockMoodleEnrollment.objects.filter(user_id=user.id, course_id=9040).exists())
        self.assertTrue(MockAzureUser.objects.get(upn=identity.institutional_upn).is_active)
        self.assertEqual(graph.get_user_license_ids(identity.institutional_upn), ('scope-a1',))

    def test_confirmation_welcome_also_reaches_an_existing_account(self):
        from apps.platform_mocks.clients import MockGraphClient, MockMoodleClient, MockMailerClient
        from apps.provisioning.dispatcher import claim_events
        from apps.provisioning.orchestrator import run_saga, run_moodle_step
        graph, moodle, mailer = MockGraphClient(), MockMoodleClient(), MockMailerClient()
        identity = self.students['POSGRADO'][0]
        graph.create_user(identity.first_name, identity.last_name, identity.institutional_upn, 'Clave de prueba')
        self.post_bulk('POSGRADO', indices=(0,))
        event_id = claim_events()[0]
        run_saga(event_id, graph=graph, moodle=moodle, mailer=mailer, dry_run=False, enqueue_moodle=lambda pk: None)
        run_moodle_step(event_id, moodle=moodle, dry_run=False)
        claim_events()
        self.assertEqual(run_saga(event_id, graph=graph, moodle=moodle, mailer=mailer, dry_run=False), 'SUCCEEDED')
        self.assertEqual(len(mailer.deliveries), 1)

    def test_ttl_preinscription_without_upn_completes_without_remote_calls(self):
        from apps.people.ttl import process_expired_affiliations
        from apps.provisioning.dispatcher import claim_events
        from apps.provisioning.orchestrator import run_saga
        from unittest.mock import Mock
        identity = self.students['POSGRADO'][0]
        Identity.objects.filter(pk=identity.pk).update(institutional_upn=None)
        Affiliation.objects.filter(pk=self.affiliations['POSGRADO'][0].pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(process_expired_affiliations()['expired'], 1)
        event_id = claim_events()[0]
        graph, moodle, mailer = Mock(), Mock(), Mock()
        self.assertEqual(run_saga(event_id, graph=graph, moodle=moodle, mailer=mailer, dry_run=False), 'SUCCEEDED')
        self.assertEqual(graph.mock_calls, [])
        self.assertEqual(moodle.mock_calls, [])
        self.assertEqual(mailer.mock_calls, [])
        self.assertEqual(OutboxEvent.objects.get(pk=event_id).step_results['moodle']['status'], 'SKIPPED_NO_UPN')

    def test_grade_monitor_preserves_institutional_events_without_exposing_them_to_posgrado(self):
        profile = ProfileTypeDefinition.objects.get(code='STAFF')
        affiliation = Affiliation.objects.create(identity=self.students['GRADO'][0], profile_type=profile,
            current_status=profile.initial_status, reference='Área de prueba')
        event = OutboxEvent.objects.create(identity=affiliation.identity, affiliation=affiliation, action='PROVISION', status='FAILED')
        response = self.client.get(reverse('portal:outbox_monitor'))
        self.assertEqual(response.context['counts']['FAILED'], 0)
        self.client.force_login(self.users['GRADO']['admin'])
        response = self.client.get(reverse('portal:outbox_monitor'))
        self.assertEqual(response.context['counts']['FAILED'], 1)
        self.assertContains(self.client.post(reverse('portal:outbox_monitor'), {'event_ids': [event.pk]}), 'Eventos rearmados: 1.')
