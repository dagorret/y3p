"""Carga secuencial de 150 ingresantes en PostgreSQL y proveedores locales."""

from collections import Counter, deque
from importlib import import_module
from io import StringIO
from time import perf_counter
from unittest import skipUnless
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase, override_settings

from apps.people.models import (
    Affiliation, AffiliationStatusHistory, Identity, OutboxEvent, PersonalProfile,
    ProfileTypeDefinition, SagaStatusDefinition,
)
from apps.provisioning.dispatcher import claim_events
from apps.provisioning.orchestrator import run_moodle_step, run_saga


@skipUnless(apps.is_installed('apps.platform_mocks'), 'Requiere ENABLE_LOCAL_MOCKS=True al iniciar Django.')
@override_settings(ENABLE_LOCAL_MOCKS=True, LUCY_DRY_RUN=False, LUCY_M365_SKU_ID='mock-admission-a1')
class MassIngresoSimulationTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('La prueba de locks requiere PostgreSQL.')
        # TransactionTestCase vacía las tablas entre pruebas; restituir catálogos.
        schema = type('SchemaEditor', (), {'connection': connection})()
        profile, _ = ProfileTypeDefinition.objects.get_or_create(
            code='ALUMNO', defaults={'name': 'Alumno', 'allows_multiple_instances': True},
        )
        SagaStatusDefinition.objects.update_or_create(
            profile_type=profile, code='INGRE', defaults={
                'name': 'Ingresante', 'provisioning_action': 'PROVISION',
                'requires_collaboration_license': True, 'default_ttl_days': 365,
            },
        )
        import_module('apps.integrations.migrations.0002_seed_admission_course_rules').seed_rules(apps, schema)
        # Ningún callback puede publicar al broker real durante estas pruebas.
        self.send_task = self.enterContext(patch('apps.people.services.current_app.send_task'))
        self.http = self.enterContext(patch('requests.sessions.Session.request', side_effect=AssertionError('Red HTTP no permitida')))
        self.smtp = self.enterContext(patch('smtplib.SMTP', side_effect=AssertionError('SMTP no permitido')))
        self.smtps = self.enterContext(patch('smtplib.SMTP_SSL', side_effect=AssertionError('SMTP SSL no permitido')))

    def command(self, name):
        call_command(name, stdout=StringIO())

    def test_mass_ingreso_150_identities_and_all_enrollments(self):
        from apps.platform_mocks.clients import MockGraphClient, MockMailerClient, MockMoodleClient
        from apps.platform_mocks.models import (
            MockAzureUser, MockMoodleCourse, MockMoodleEnrollment, MockMoodleUser, MockSialRecord,
        )

        start = perf_counter()
        self.command('seed_mock_sial')
        self.command('seed_mock_sial')
        self.assertEqual(MockSialRecord.objects.count(), 150)
        records = list(MockSialRecord.objects.order_by('dni'))
        self.assertEqual(len({r.dni for r in records}), 150)
        self.assertEqual(Counter(r.commission for r in records), {'COMISION 1': 50, 'COMISION 2': 50, None: 50})
        self.assertEqual(sum(r.career_code == '7' for r in records), 25)
        self.assertEqual(sum(r.career_code == '8' and r.modality == 'D' for r in records), 25)
        self.assertEqual({r.career_code for r in records[:50]}, {'2', '3', '4'})
        self.assertEqual({r.career_code for r in records[50:100]}, {'2', '3', '4'})
        for record in records:
            record.full_clean()
            self.assertRegex(record.dni, r'^[0-9]{8}$')
            self.assertEqual(record.first_name, record.first_name.strip())
            self.assertEqual(record.last_name, record.last_name.strip())
            self.assertRegex(record.personal_email, r'@(gmail|hotmail)\.com$')
        self.command('sync_sial_ingreso')
        ingestion_time = perf_counter() - start
        self.assertEqual(Identity.objects.count(), 150)
        self.assertEqual(Affiliation.objects.count(), 150)
        self.assertEqual(AffiliationStatusHistory.objects.count(), 150)
        self.assertEqual(OutboxEvent.objects.filter(status='PENDING').count(), 150)
        self.assertEqual(self.send_task.call_count, 150)
        # Repetir la ingesta no duplica personas, vinculaciones ni eventos.
        self.command('sync_sial_ingreso')
        self.assertEqual(OutboxEvent.objects.count(), 150)
        self.assertEqual(self.send_task.call_count, 150)

        MockMoodleCourse.objects.bulk_create([
            MockMoodleCourse(shortname=f'I{i}', fullname=f'Curso de ingreso {i}') for i in range(1, 9)
        ])
        graph, moodle, mailer = MockGraphClient(), MockMoodleClient(), MockMailerClient()
        moodle_queue = deque()
        processing_start = perf_counter()
        passes = 0
        while OutboxEvent.objects.exclude(status=OutboxEvent.Status.SUCCEEDED).exists():
            passes += 1
            self.assertLessEqual(passes, 10, 'El Outbox no converge; hay eventos fallidos o retenidos.')
            claimed = claim_events(limit=100)
            self.assertTrue(claimed, 'Hay eventos sin resolver pero ninguno puede reclamarse.')
            for event_id in claimed:
                result = run_saga(event_id, graph=graph, moodle=moodle, mailer=mailer,
                                  dry_run=False, enqueue_moodle=moodle_queue.append)
                self.assertIn(result, (OutboxEvent.Status.WAITING_SUBTASK, OutboxEvent.Status.SUCCEEDED))
            while moodle_queue:
                self.assertEqual(run_moodle_step(moodle_queue.popleft(), moodle=moodle, dry_run=False),
                                 OutboxEvent.Status.PENDING)
        processing_time = perf_counter() - processing_start

        self.assertEqual(OutboxEvent.objects.filter(status='SUCCEEDED').count(), 150)
        self.assertEqual(OutboxEvent.objects.exclude(status='SUCCEEDED').count(), 0)
        self.assertEqual(MockAzureUser.objects.count(), 150)
        self.assertEqual(MockMoodleUser.objects.count(), 150)
        self.assertEqual(MockMoodleEnrollment.objects.count(), 550)
        self.assertEqual(MockMoodleEnrollment.objects.exclude(group_name__isnull=True).exclude(group_name='').count(), 500)
        self.assertEqual(len(mailer.deliveries), 150)
        self.assertEqual(set(mailer.deliveries), {(r.personal_email, f'a{r.dni}@eco.unrc.edu.ar') for r in records})
        for record in records:
            upn = f'a{record.dni}@eco.unrc.edu.ar'
            azure = MockAzureUser.objects.get(upn=upn)
            self.assertEqual(azure.document_number, record.dni)
            self.assertEqual(azure.assigned_sku, 'mock-admission-a1')
            self.assertTrue(azure.password_profile_force_change)
            user = MockMoodleUser.objects.get(username=upn)
            self.assertEqual(user.email, upn)
            self.assertEqual(user.auth_plugin, 'oidc')
            if record.career_code in ('2', '3', '4'):
                expected = {'I3', 'I4', 'I5', 'I6', 'I1' if record.commission == 'COMISION 1' else 'I2'}
                expected_group = record.commission
            else:
                expected, expected_group = {f'I{record.career_code}'}, None
            enrollments = list(user.enrollments.select_related('course'))
            self.assertEqual({e.course.shortname for e in enrollments}, expected)
            self.assertTrue(all(e.group_name == expected_group and e.role_id == 5 for e in enrollments))
            affiliation = Affiliation.objects.get(identity__identifier=record.dni)
            self.assertEqual(affiliation.modality, 'DISTANCIA' if record.modality == 'D' else 'PRESENCIAL')
            event = affiliation.outbox_events.get()
            self.assertEqual(event.payload['modality'], record.modality)
            self.assertEqual(event.payload['commission'], record.commission or '')
            self.assertTrue(all(not step['simulated'] for step in event.step_results.values()))
            self.assertIsNone(event.locked_at)
            self.assertIsNotNone(event.processed_at)
            self.assertEqual(event.attempts, 0)

        self.assertFalse(connection.in_atomic_block)
        self.assertTrue(connection.get_autocommit())
        self.assertFalse(connection.needs_rollback)
        with connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() '
                           "AND (state LIKE 'idle in transaction%%' OR cardinality(pg_blocking_pids(pid)) > 0)")
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute('SELECT count(*) FROM pg_prepared_xacts WHERE database = current_database()')
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM pg_locks WHERE pid = pg_backend_pid() AND locktype = 'transactionid'")
            self.assertEqual(cursor.fetchone()[0], 0)
        self.http.assert_not_called()
        self.smtp.assert_not_called()
        self.smtps.assert_not_called()
        print(f'\nMASIVIDAD: 150 eventos SUCCEEDED; 150 cuentas licenciadas; 550 matrículas; '
              f'500 grupos; ingesta={ingestion_time:.3f}s; procesamiento={processing_time:.3f}s; '
              f'pasadas={passes}; sin bloqueos ni transacciones pendientes.')

    def test_record_failure_rolls_back_identity_contact_affiliation_and_event(self):
        from apps.people.services import create_affiliation
        from apps.platform_mocks.models import MockSialRecord
        from apps.platform_mocks.services import ingest_mock_record

        record = MockSialRecord.objects.create(
            dni='90000999', first_name='Ana', last_name='Pérez', personal_email='ana@gmail.com',
            condition='INGRESANTE', career_code='2', modality='P', commission='COMISION 1',
        )
        def fail_after_event(*args, **kwargs):
            affiliation = create_affiliation(*args, **kwargs)
            self.assertEqual(affiliation.outbox_events.count(), 1)
            raise ValidationError('Fallo posterior a la emisión del evento')

        with patch('apps.platform_mocks.services.create_affiliation', side_effect=fail_after_event):
            with self.assertRaises(ValidationError):
                ingest_mock_record(record)
        self.assertFalse(Identity.objects.filter(identifier=record.dni).exists())
        self.assertEqual(PersonalProfile.objects.count(), 0)
        self.assertEqual(Affiliation.objects.count(), 0)
        self.assertEqual(OutboxEvent.objects.count(), 0)
        self.send_task.assert_not_called()
