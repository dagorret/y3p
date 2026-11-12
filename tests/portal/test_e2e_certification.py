"""Recorrido integral pre-seguridad sobre PostgreSQL y proveedores locales persistentes.

La prueba concatena navegación por rol, semilla, espejo relacional, promoción
atómica, saga con fallo SMTP y rearme auditado, TTL y accesos compartidos. Un
transporte SMTP falso ejecuta MailerClient real sin abrir sockets. Los secretos
canarios se generan en memoria y se buscan en Outbox, auditoría y registros;
ningún valor canario forma parte de la evidencia impresa de certificación.
"""

import json
import logging
import secrets
import smtplib
import sys
from datetime import timedelta
from html.parser import HTMLParser
from io import StringIO
from unittest.mock import MagicMock, patch

import django
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.integrations.catalogs import sync_external_catalogs
from apps.integrations.models import CatalogSyncState, TeamsWorkspaceMirror, TeamsChannelMirror, TeamsMemberMirror, MoodleCourseMirror
from apps.integrations.services.mailer import MailerClient
from apps.people.models import Identity, Affiliation, OutboxEvent, SagaStatusDefinition
from apps.people.scopes import SCOPES, has_scope_permission, scope_url
from apps.people.tasks import process_expired_affiliations
from apps.platform_mocks.clients import MockGraphClient, MockMoodleClient
from apps.platform_mocks.models import MockAzureUser, MockMoodleEnrollment
from apps.portal.student_grid import student_grid_context
from apps.provisioning.dispatcher import claim_events
from apps.provisioning.orchestrator import run_saga, run_moodle_step, _temporary_password


class NavigationParser(HTMLParser):
    """Distingue cabecera y lateral para comprobar enlaces reales, no coincidencias de texto."""

    def __init__(self):
        super().__init__()
        self.zone = None
        self.links = {'header': [], 'aside': []}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ('header', 'aside'):
            self.zone = tag
        elif tag == 'a' and self.zone:
            self.links[self.zone].append(attributes)

    def handle_endtag(self, tag):
        if tag == self.zone:
            self.zone = None


class SecretAuditHandler(logging.StreamHandler):
    """Captura texto, campos adicionales y excepciones sin persistir los valores canarios."""

    def __init__(self, stream):
        super().__init__(stream)
        self.records = []

    def emit(self, record):
        super().emit(record)
        self.records.append(dict(record.__dict__))


@override_settings(ENABLE_LOCAL_MOCKS=True, LUCY_DRY_RUN=False, LUCY_M365_SKU_ID='certificacion-a1')
class PreSecurityE2ECertificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {}
        for scope in SCOPES:
            prefix = 'alumnos' if scope == 'GRADO' else scope.lower()
            for role in ('operador', 'admin'):
                user = get_user_model().objects.create_user(f'certificacion-{scope}-{role}')
                user.groups.add(Group.objects.get(name=f'adm_{prefix}_rol_{role}'))
                cls.users[scope, role] = user
        cls.global_admin = get_user_model().objects.create_superuser('certificacion-global')
        cls.multiple_admin = get_user_model().objects.create_user('certificacion-multiambito')
        cls.multiple_admin.groups.add(*Group.objects.filter(name__in=[
            'adm_alumnos_rol_admin', 'adm_posgrado_rol_admin', 'adm_extension_rol_admin']))

    def monitor_profiles(self, response):
        return set(response.context['events_page'].paginator.object_list.values_list('affiliation__profile_type__code', flat=True))

    def process_claimed(self, event_id, graph, moodle, mailer):
        """Ejecuta la continuación real de la saga después de cada reclamación persistida."""
        state = run_saga(event_id, graph=graph, moodle=moodle, mailer=mailer, dry_run=False, enqueue_moodle=lambda pk: None)
        if state == 'WAITING_SUBTASK':
            self.assertEqual(run_moodle_step(event_id, moodle=moodle, dry_run=False), 'PENDING')
            self.assertIn(event_id, claim_events())
            state = run_saga(event_id, graph=graph, moodle=moodle, mailer=mailer, dry_run=False)
        return state

    def assert_secret_free(self, values, canaries):
        """No imprime secretos al fallar; inspecciona también las claves sensibles del JSON."""
        text = json.dumps(values, ensure_ascii=False, default=str)
        self.assertFalse(any(secret in text for secret in canaries), 'Se detectó un secreto canario persistido o registrado.')
        forbidden = {'password', 'access_token', 'refresh_token', 'client_secret', 'smtp_password', 'authorization'}
        def inspect(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertFalse(key.lower() in forbidden and bool(child) and child != '[REDACTED]', 'Se detectó un campo secreto persistido.')
                    inspect(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    inspect(child)
        inspect(values)

    def test_integral_pre_security_certification(self):
        self.assertEqual(sys.version_info[:3], (3, 12, 15))
        self.assertEqual(django.get_version(), '6.1.2')
        self.assertEqual((connection.vendor, connection.pg_version), ('postgresql', 160015))
        canaries, log_stream = [], StringIO()
        handler = SecretAuditHandler(log_stream)
        smtp_logger = logging.getLogger('apps.integrations.certificacion.smtp')
        original_level, original_propagate = smtp_logger.level, smtp_logger.propagate
        smtp_logger.setLevel(logging.INFO)
        smtp_logger.propagate = False
        smtp_logger.addHandler(handler)
        logging.getLogger().addHandler(handler)
        def restore_logging():
            smtp_logger.removeHandler(handler)
            smtp_logger.setLevel(original_level)
            smtp_logger.propagate = original_propagate
            logging.getLogger().removeHandler(handler)
            handler.close()
        self.addCleanup(restore_logging)
        self.enterContext(patch('requests.sessions.Session.request', side_effect=AssertionError('La certificación local no consulta proveedores reales.')))
        self.enterContext(patch('smtplib.SMTP', side_effect=AssertionError('La certificación no abre conexiones SMTP reales.')))
        self.enterContext(patch('apps.people.services.current_app.send_task'))
        def canary_password(*args, **kwargs):
            value = _temporary_password(*args, **kwargs)
            canaries.append(value)
            return value
        self.enterContext(patch('apps.provisioning.orchestrator._temporary_password', side_effect=canary_password))

        with self.subTest(etapa='Semilla poblada y catálogos idempotentes'):
            call_command('seed_dev_students', count=60, stdout=StringIO())
            for scope, expected in (('GRADO', 60), ('POSGRADO', 20), ('EXTENSION', 20)):
                self.assertEqual(Affiliation.objects.filter(profile_type__code=SCOPES[scope][0],
                    identity__metadata__seed__in=['seed_dev_students', 'seed_dev_academic_students']).count(), expected)
            mirrors = (TeamsWorkspaceMirror, TeamsChannelMirror, TeamsMemberMirror, MoodleCourseMirror)
            self.assertEqual(tuple(model.objects.count() for model in mirrors), (3, 6, 61, 3))
            keys = [list(model.objects.order_by('pk').values_list('pk', flat=True)) for model in mirrors]
            for _ in range(2):
                self.assertEqual(sync_external_catalogs(), {'TEAMS': 'SUCCEEDED', 'MOODLE': 'SUCCEEDED'})
                self.assertEqual([list(model.objects.order_by('pk').values_list('pk', flat=True)) for model in mirrors], keys)
            self.assertEqual(CatalogSyncState.objects.filter(last_status='SUCCEEDED', last_run_at__isnull=False, next_run_at__gt=timezone.now()).count(), 2)

        with self.subTest(etapa='Navegación exclusiva por rol y sin duplicación lateral'):
            for scope in SCOPES:
                for role in ('operador', 'admin'):
                    self.client.force_login(self.users[scope, role])
                    response = self.client.get(scope_url(scope))
                    self.assertEqual(response.status_code, 200)
                    parser = NavigationParser()
                    parser.feed(response.content.decode())
                    self.assertEqual([item.get('data-scope') for item in parser.links['header'] if item.get('data-scope')], [scope])
                    self.assertEqual(sum(item['href'] == scope_url(scope) for item in parser.links['aside']), 1)
                    self.assertFalse(any(item['href'] == scope_url(other) for item in parser.links['aside'] for other in SCOPES if other != scope))
                    for other in SCOPES:
                        if other != scope:
                            self.assertEqual(self.client.get(scope_url(other)).status_code, 403)
                            self.assertEqual(self.client.get(scope_url(other, 'export')).status_code, 403)
            self.client.force_login(self.global_admin)
            for scope in SCOPES:
                parser = NavigationParser()
                parser.feed(self.client.get(scope_url(scope)).content.decode())
                self.assertEqual({item.get('data-scope') for item in parser.links['header'] if item.get('data-scope')}, set(SCOPES))
                self.assertFalse(any(item['href'] == scope_url(other) for item in parser.links['aside'] for other in SCOPES if other != scope))

        with self.subTest(etapa='Monitor aislado incluso para administradores no globales con varios roles'):
            monitor_url = reverse('portal:outbox_monitor')
            for scope in SCOPES:
                self.client.force_login(self.users[scope, 'admin'])
                self.assertEqual(self.monitor_profiles(self.client.get(monitor_url)), {SCOPES[scope][0]})
                self.assertEqual(self.client.get(monitor_url, {'scope': next(code for code in SCOPES if code != scope)}).status_code, 403)
            self.client.force_login(self.multiple_admin)
            self.assertEqual(self.monitor_profiles(self.client.get(monitor_url)), {'ALUMNO'})
            self.assertEqual(self.monitor_profiles(self.client.get(monitor_url, {'scope': 'POSGRADO'})), {'POSGRADO'})
            badge = self.client.get(reverse('portal:outbox_badge'), {'scope': 'POSGRADO'})
            self.assertContains(badge, 'scope=POSGRADO')
            self.client.force_login(self.global_admin)
            self.assertEqual(self.monitor_profiles(self.client.get(monitor_url)), {'ALUMNO', 'POSGRADO', 'EXTENSION'})

        graph, moodle = MockGraphClient(), MockMoodleClient()
        with self.subTest(etapa='Confirmación atómica e idempotente en Posgrado y Extensión'):
            events = {}
            for scope in ('POSGRADO', 'EXTENSION'):
                self.client.force_login(self.users[scope, 'operador'])
                people = list(Identity.objects.filter(metadata__scope=scope).order_by('identifier')[:2])
                data = {'action': 'confirm', 'identity_ids': [person.pk for person in people]}
                self.assertContains(self.client.post(scope_url(scope, 'bulk'), data, HTTP_HX_REQUEST='true'), 'Confirmados: 1. Omitidos: 1.')
                self.assertContains(self.client.post(scope_url(scope, 'bulk'), data, HTTP_HX_REQUEST='true'), 'Confirmados: 0. Omitidos: 2.')
                event = OutboxEvent.objects.get(identity=people[0], status='PENDING')
                self.assertEqual((event.action, event.requested_by_id), ('PROVISION', self.users[scope, 'operador'].pk))
                self.assertEqual(event.payload['profile_type_code'], scope)
                events[scope] = event
            self.assertEqual(set(claim_events()), {event.pk for event in events.values()})

        smtp_secret = secrets.token_urlsafe(36)
        canaries.append(smtp_secret)
        server, context = MagicMock(), MagicMock()
        context.__enter__.return_value = server
        failed_once = False
        def smtp_delivery(message):
            nonlocal failed_once
            if not failed_once:
                failed_once = True
                #FE2ESECRETS01-10901: Inyecta un secreto solo en memoria para verificar que el fallo SMTP no lo registre.
                raise smtplib.SMTPDataError(451, f'Fallo canario {canaries[-1]} {smtp_secret}'.encode())
            return {}
        server.send_message.side_effect = smtp_delivery
        mailer = MailerClient(host='smtp.certificacion.invalid', port=587, username='certificacion@example.test',
            password=smtp_secret, from_email='certificacion@example.test', smtp_factory=MagicMock(return_value=context), logger=smtp_logger)
        with self.subTest(etapa='Fallo SMTP, protección de secretos y rearme auditado'):
            failed = events['POSGRADO']
            OutboxEvent.objects.filter(pk=failed.pk).update(max_attempts=1)
            self.assertEqual(self.process_claimed(failed.pk, graph, moodle, mailer), 'FAILED')
            self.assertEqual(self.process_claimed(events['EXTENSION'].pk, graph, moodle, mailer), 'SUCCEEDED')
            failed.refresh_from_db()
            self.assertEqual(failed.attempts, 1)
            self.assertIn('graph_user', failed.step_results)
            self.assertIn('No se pudo entregar el correo.', log_stream.getvalue())
            audit_data = list(OutboxEvent.objects.values('payload', 'step_results', 'last_error'))
            self.assert_secret_free(audit_data + [log_stream.getvalue(), handler.records], canaries)
            self.client.force_login(self.users['POSGRADO', 'operador'])
            self.assertEqual(self.client.post(monitor_url, {'event_ids': [failed.pk]}).status_code, 403)
            self.client.force_login(self.multiple_admin)
            other_id = OutboxEvent.objects.filter(affiliation__profile_type__code='ALUMNO', status='FAILED').first().pk
            self.assertEqual(self.client.post(monitor_url, {'scope': 'POSGRADO', 'event_ids': [failed.pk, other_id]}).status_code, 403)
            failed.refresh_from_db()
            self.assertEqual(failed.status, 'FAILED')
            self.client.force_login(self.users['POSGRADO', 'admin'])
            self.assertContains(self.client.post(monitor_url, {'event_ids': [failed.pk]}, HTTP_HX_REQUEST='true'), 'Eventos rearmados: 1.')
            self.assertTrue(LogEntry.objects.filter(user=self.users['POSGRADO', 'admin'], object_id=str(failed.pk), change_message__contains='rearme_outbox').exists())
            self.assertIn(failed.pk, claim_events())
            self.assertEqual(self.process_claimed(failed.pk, graph, moodle, mailer), 'SUCCEEDED')
            self.assertEqual(server.send_message.call_count, 3)
            self.assertEqual(run_saga(failed.pk, graph=graph, moodle=moodle, mailer=mailer, dry_run=False), 'SUCCEEDED')
            self.assertEqual(server.send_message.call_count, 3)

        with self.subTest(etapa='TTL preserva accesos compartidos y retira la última vinculación'):
            person = events['POSGRADO'].identity
            graph.assign_license(person.institutional_upn, 'certificacion-a1')
            status = SagaStatusDefinition.objects.get(profile_type__code='EXTENSION', code='ALUMNO')
            status.requires_collaboration_license = True
            status.save()
            shared = Affiliation.objects.create(identity=person, profile_type=status.profile_type, current_status=status,
                reference='9040', expires_at=timezone.now() + timedelta(days=30))
            Affiliation.objects.filter(pk=events['POSGRADO'].affiliation_id).update(expires_at=timezone.now() - timedelta(seconds=1))
            self.assertGreaterEqual(process_expired_affiliations.run()['expired'], 1)
            for event_id in claim_events():
                self.assertEqual(self.process_claimed(event_id, graph, moodle, mailer), 'SUCCEEDED')
            self.assertTrue(MockAzureUser.objects.get(upn=person.institutional_upn).is_active)
            self.assertEqual(graph.get_user_license_ids(person.institutional_upn), ('certificacion-a1',))
            self.assertTrue(MockMoodleEnrollment.objects.filter(user__username=person.institutional_upn, course__shortname='9040').exists())
            Affiliation.objects.filter(pk=shared.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
            self.assertEqual(process_expired_affiliations.run()['expired'], 1)
            for event_id in claim_events():
                self.assertEqual(self.process_claimed(event_id, graph, moodle, mailer), 'SUCCEEDED')
            self.assertFalse(MockAzureUser.objects.get(upn=person.institutional_upn).is_active)
            self.assertEqual(graph.get_user_license_ids(person.institutional_upn), ())
            self.assertFalse(MockMoodleEnrollment.objects.filter(user__username=person.institutional_upn, course__shortname='9040').exists())
            self.assertEqual(process_expired_affiliations.run()['expired'], 0)

        with self.subTest(etapa='Siete consultas por ámbito y auditoría final de secretos'):
            for scope in SCOPES:
                user = self.users[scope, 'operador']
                has_scope_permission(user, scope, 'admin')
                for params in ({'page_size': '25'}, {'q': Identity.objects.filter(affiliations__profile_type__code=SCOPES[scope][0]).first().identifier}):
                    with self.assertNumQueries(7):
                        grid = student_grid_context(params, user=user, scope=scope)
                    with self.assertNumQueries(0):
                        render_to_string('portal/fragments/administracion_results.html', grid)
            evidence = list(OutboxEvent.objects.values('payload', 'step_results', 'last_error'))
            evidence += list(LogEntry.objects.values_list('change_message', flat=True))
            self.assert_secret_free(evidence + [log_stream.getvalue(), handler.records], canaries)
        print('\nE2E PRE-SEGURIDAD: navegación por rol, monitor aislado, semilla 60/20/20, espejos 3/6/61/3, sincronización idempotente, confirmación y bienvenida, fallo SMTP y rearme auditado, TTL compartido y baja final: OK.')
        print(f'E2E CONSULTAS: siete por página en GRADO, POSGRADO y EXTENSION; renderizado sin consultas: OK. Auditoría de {len(canaries)} secretos canarios en memoria, sin valores persistidos en Outbox ni registros: OK.')
