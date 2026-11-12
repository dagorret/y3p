"""Ficha 360°, permisos, edición y soporte contra clientes interceptados."""

import json
from datetime import date
from unittest.mock import Mock, patch
from unittest import skipUnless

from django.apps import apps

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.integrations.dataclasses import ExternalActionResult, MailDeliveryResult, MoodleEnrollmentResult, MoodleGroupResult, MoodleUserResult
from apps.integrations.exceptions import RemoteServiceUnavailableError
from apps.people.models import Affiliation, AffiliationStatusHistory, Identity, PersonalProfile, ProfileTypeDefinition, SagaStatusDefinition, SagaTransitionRule


@override_settings(LUCY_M365_SKU_ID='sku-a1', LUCY_DRY_RUN=False)
class AdminSuiteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.operator = get_user_model().objects.create_superuser('operator360', password='test')
        cls.identity = Identity.objects.create(category='FISICA', identifier='33123456',
            first_name='Ana', last_name='Pérez', institutional_upn='a33123456@eco.unrc.edu.ar', metadata={'legajo': 'L-123'})
        cls.contact = PersonalProfile.objects.create(identity=cls.identity, personal_email='ana@example.test', phone='1234')
        profile = ProfileTypeDefinition.objects.get(code='ALUMNO')
        cls.pre = SagaStatusDefinition.objects.get(profile_type=profile, code='PRE')
        cls.asp = SagaStatusDefinition.objects.get(profile_type=profile, code='ASP')
        cls.affiliation = Affiliation.objects.create(identity=cls.identity, profile_type=profile, current_status=cls.pre, reference='2')
        cls.rule = SagaTransitionRule.objects.get(profile_type=profile, from_status=cls.pre, to_status=cls.asp)

    def setUp(self):
        self.client.force_login(self.operator)

    def url(self, name, **extra):
        return reverse('portal:' + name, kwargs={'identity_id': self.identity.pk, **extra})

    def post(self, name, data=None, **extra):
        return self.client.post(self.url(name, **extra), data or {}, HTTP_HX_REQUEST='true')

    def test_search_by_dni_name_surname_and_upn_and_render_full_card(self):
        for query in ('33123456', 'Ana', 'Pérez', 'a33123456@eco.unrc.edu.ar'):
            response = self.client.get(reverse('portal:administracion_buscar'), {'q': query}, HTTP_HX_REQUEST='true')
            self.assertContains(response, 'Abrir Ficha 360°')
            self.assertNotContains(response, '<html')
        response = self.client.get(self.url('identity360'))
        for text in ('FICHA MAESTRA 360°', 'Moodle Express', 'legajo', 'Historial inmutable'):
            self.assertContains(response, text)

    def test_anonymous_and_nonstaff_cannot_read_or_mutate(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url('identity360')).status_code, 302)
        self.assertEqual(self.post('identity_sessions').status_code, 302)
        user = get_user_model().objects.create_user('no-staff', password='test')
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url('identity360')).status_code, 403)
        self.assertEqual(self.post('identity_contact').status_code, 403)

    def test_read_only_staff_cannot_execute_support_and_get_does_not_mutate(self):
        user = get_user_model().objects.create_user('reader', is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='can_view_student_360'))
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url('identity360')).status_code, 200)
        with patch('apps.people.support.build_graph') as factory:
            self.assertEqual(self.post('identity_sessions').status_code, 403)
            factory.assert_not_called()
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(self.url('identity_sessions')).status_code, 405)

    def test_csrf_is_required_on_mutations(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.operator)
        self.assertEqual(client.post(self.url('identity_sessions')).status_code, 403)

    def test_edit_contact_and_add_remove_metadata_without_reload(self):
        response = self.post('identity_contact', {'first_name': 'Ana María', 'last_name': 'Pérez',
            'personal_email': 'nueva@example.test', 'phone': '358123456', 'city': 'Río Cuarto', 'address': 'Calle 123'})
        self.assertContains(response, 'Datos de contacto actualizados')
        self.assertNotContains(response, '<html')
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.phone, '358123456')
        self.assertEqual(self.contact.phone_number, '358123456')
        self.assertEqual(self.contact.personal_email, 'nueva@example.test')
        self.post('identity_metadata', {'operation': 'add', 'key': 'tramite', 'value': '{"numero": 42}'})
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.metadata, {'legajo': 'L-123', 'tramite': {'numero': 42}})
        self.post('identity_metadata', {'operation': 'remove', 'key': 'legajo'})
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.metadata, {'tramite': {'numero': 42}})
        self.assertTrue(LogEntry.objects.filter(user=self.operator, object_id=str(self.identity.pk)).exists())

    def test_invalid_contact_does_not_partially_update_identity(self):
        response = self.post('identity_contact', {'first_name': 'Cambiar', 'last_name': '', 'personal_email': 'bad'})
        self.assertContains(response, 'role="status"')
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.first_name, 'Ana')

    def graph_mock(self):
        graph = Mock()
        graph.reset_password_temporary.return_value = ExternalActionResult('reset_password')
        graph.revoke_sign_in_sessions.return_value = ExternalActionResult('revoke_sessions')
        graph.get_user_license_ids.return_value = ()
        graph.toggle_license.return_value = ExternalActionResult('assign_license')
        graph.toggle_user_status.return_value = ExternalActionResult('toggle_status')
        return graph

    def test_credentials_generate_safe_password_send_it_and_never_expose_secret(self):
        graph, mailer = self.graph_mock(), Mock(dry_run=False)
        mailer.send_credentials_email.return_value = MailDeliveryResult('ana@example.test', 'Credenciales', True)
        with patch('apps.people.support.build_graph', return_value=graph), patch('apps.people.support.build_mailer', return_value=mailer):
            response = self.post('identity_credentials')
        self.assertContains(response, 'Clave temporal regenerada y enviada')
        password = graph.reset_password_temporary.call_args.args[1]
        self.assertGreaterEqual(len(password), 24)
        for pattern in (r'[A-Z]', r'[a-z]', r'[0-9]', r'[^A-Za-z0-9]'):
            self.assertRegex(password, pattern)
        self.assertEqual(mailer.send_credentials_email.call_args.args[-1], password)
        self.assertNotContains(response, password)
        self.assertFalse(LogEntry.objects.filter(change_message__contains=password).exists())

    def test_failed_mail_is_audited_as_partial_without_claiming_success(self):
        graph, mailer = self.graph_mock(), Mock(dry_run=False)
        mailer.send_credentials_email.return_value = MailDeliveryResult('ana@example.test', 'Credenciales', False)
        with patch('apps.people.support.build_graph', return_value=graph), patch('apps.people.support.build_mailer', return_value=mailer):
            response = self.post('identity_credentials')
        self.assertNotContains(response, 'Clave temporal regenerada y enviada')
        self.assertTrue(LogEntry.objects.filter(change_message__contains='PARTIAL').exists())

    def test_revoke_sessions_and_toggle_license_both_directions(self):
        graph = self.graph_mock()
        with patch('apps.people.support.build_graph', return_value=graph):
            self.assertContains(self.post('identity_sessions'), 'Sesiones revocadas')
            self.post('identity_license')
            graph.toggle_license.assert_called_with(self.identity.institutional_upn, 'sku-a1', assign=True)
            graph.get_user_license_ids.return_value = ('sku-a1',)
            graph.toggle_license.return_value = ExternalActionResult('remove_license')
            self.post('identity_license')
            graph.toggle_license.assert_called_with(self.identity.institutional_upn, 'sku-a1', assign=False)
        graph.revoke_sign_in_sessions.assert_called_once_with(self.identity.institutional_upn)

    def test_suspend_updates_local_affiliations_and_history_only_after_confirmation(self):
        graph = self.graph_mock()
        with patch('apps.people.support.build_graph', return_value=graph):
            self.post('identity_suspend')
        self.affiliation.refresh_from_db()
        self.assertFalse(self.affiliation.is_active)
        self.assertEqual(self.affiliation.history.get().changed_by_user, self.operator)

    def test_failed_or_simulated_suspend_does_not_deactivate_local_affiliations(self):
        graph = self.graph_mock()
        graph.toggle_user_status.side_effect = RemoteServiceUnavailableError('offline')
        with patch('apps.people.support.build_graph', return_value=graph):
            self.post('identity_suspend')
        self.affiliation.refresh_from_db()
        self.assertTrue(self.affiliation.is_active)
        graph.toggle_user_status.side_effect = None
        graph.toggle_user_status.return_value = ExternalActionResult('toggle_status', simulated=True)
        with patch('apps.people.support.build_graph', return_value=graph):
            self.assertContains(self.post('identity_suspend'), 'SIMULACIÓN')
        self.affiliation.refresh_from_db()
        self.assertTrue(self.affiliation.is_active)

    def test_manual_transition_writes_actor_and_reason_and_rejects_stale_retry(self):
        data = {'rule': self.rule.pk, 'expected_from_code': 'PRE', 'reason': 'Confirmación de ventanilla'}
        self.assertContains(self.post('affiliation_transition', data, affiliation_id=self.affiliation.pk), 'Transición aplicada')
        history = AffiliationStatusHistory.objects.get(affiliation=self.affiliation)
        self.assertEqual(history.changed_by_user, self.operator)
        self.assertEqual(history.reason, data['reason'])
        self.post('affiliation_transition', data, affiliation_id=self.affiliation.pk)
        self.assertEqual(self.affiliation.history.count(), 1)

    def test_ttl_update_is_audited_and_cross_identity_affiliation_is_rejected(self):
        response = self.post('affiliation_ttl', {'expires_at': '2028-12-31', 'reason': 'Prórroga autorizada'}, affiliation_id=self.affiliation.pk)
        self.assertContains(response, 'Vigencia actualizada')
        self.affiliation.refresh_from_db()
        self.assertEqual(timezone.localtime(self.affiliation.expires_at).date(), date(2028, 12, 31))
        self.assertEqual(self.affiliation.history.get().changed_by_user, self.operator)
        other = Identity.objects.create(category='FISICA', identifier='99999999', first_name='Otra', last_name='Persona')
        response = self.client.post(reverse('portal:affiliation_ttl', args=[other.pk, self.affiliation.pk]), {'expires_at': '2030-01-01', 'reason': 'No'})
        self.assertEqual(response.status_code, 404)

    def test_moodle_express_enrol_group_and_unenrol(self):
        moodle = Mock(dry_run=False)
        moodle.get_user_by_username.return_value = MoodleUserResult(88, 'ana', 'ana@example.test', False)
        moodle.get_course_by_shortname.return_value = {'id': 55}
        moodle.enrol_user.return_value = MoodleEnrollmentResult(88, 55, 3, True)
        moodle.get_or_create_group.return_value = 77
        moodle.add_user_to_group.return_value = MoodleGroupResult(77, 55, 88, 'add', True)
        moodle.unenrol_user.return_value = ExternalActionResult('unenrol_user')
        with patch('apps.people.support.build_moodle', return_value=moodle):
            self.assertContains(self.post('identity_moodle_enrol', {'shortname': 'I1', 'role_id': 3, 'group_name': 'COMISION 1'}), 'Operación Moodle completada')
            self.post('identity_moodle_unenrol', {'shortname': 'I1', 'role_id': 5})
        moodle.enrol_user.assert_called_once_with(88, 55, role_id=3)
        moodle.add_user_to_group.assert_called_once_with(77, 88, course_id=55)
        moodle.unenrol_user.assert_called_once_with(88, 55, role_id=5)

    def test_admin_actions_require_confirmation_and_work_for_individual_and_batch(self):
        graph = self.graph_mock()
        url = reverse('admin:people_identity_changelist')
        data = {'action': 'action_revocar_sesiones_mfa', '_selected_action': self.identity.pk}
        with patch('apps.people.support.build_graph', return_value=graph):
            self.assertContains(self.client.post(url, data), 'Confirmar operación')
            graph.revoke_sign_in_sessions.assert_not_called()
            self.assertEqual(self.client.post(url, {**data, 'confirm_support': 'yes'}).status_code, 302)
            detail_url = reverse('admin:people_identity_sessions_detail', args=[self.identity.pk])
            self.assertContains(self.client.get(detail_url), 'Confirmar operación')
            self.assertEqual(self.client.post(detail_url, {'confirm_support': 'yes'}).status_code, 302)
        self.assertEqual(graph.revoke_sign_in_sessions.call_count, 2)


@skipUnless(apps.is_installed('apps.platform_mocks'), 'Requiere ENABLE_LOCAL_MOCKS=True.')
@override_settings(ENABLE_LOCAL_MOCKS=True, LUCY_M365_SKU_ID='mock-a1', LUCY_DRY_RUN=False)
class GlobalAdministrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.operator = get_user_model().objects.create_superuser('global-operator', password='test')

    def setUp(self):
        from apps.platform_mocks.clients import MockGraphClient
        self.client.force_login(self.operator)
        self.graph = MockGraphClient()
        self.enterContext(patch('apps.portal.global_services.build_graph', return_value=self.graph))
        self.enterContext(patch('apps.people.services.current_app.send_task'))
        self.http = self.enterContext(patch('requests.sessions.Session.request', side_effect=AssertionError('Red no permitida')))

    def tearDown(self):
        self.http.assert_not_called()

    def person_data(self, **overrides):
        data = {'dni': '44123456', 'first_name': 'María', 'last_name': 'Torres',
                'personal_email': 'maria@example.test', 'reference': '2', 'role': 'ALUMNO'}
        return {**data, **overrides}

    def functional_data(self, **overrides):
        data = {'identifier': 'CATEDRA_ECO', 'first_name': 'Cátedra de Economía',
                'institutional_upn': 'catedra.eco@eco.unrc.edu.ar',
                'personal_email': 'responsable@example.test', 'reference': 'Economía'}
        return {**data, **overrides}

    def team_owner(self, upn='docente@eco.unrc.edu.ar'):
        from apps.platform_mocks.models import MockAzureUser
        return MockAzureUser.objects.create(upn=upn, first_name='Docente', last_name='Responsable', document_number='')

    def create_team(self):
        self.team_owner()
        response = self.client.post(reverse('portal:team_create'), {'display_name': 'Cátedra Economía',
            'description': 'Comisión de ingreso', 'owner_upn': 'docente@eco.unrc.edu.ar'}, HTTP_HX_REQUEST='true')
        self.assertEqual(response['HX-Redirect'], reverse('portal:teams'))
        from apps.platform_mocks.models import MockTeam
        return MockTeam.objects.get()

    def drain(self):
        from apps.platform_mocks.clients import MockMailerClient, MockMoodleClient
        from apps.provisioning.dispatcher import claim_events
        from apps.provisioning.orchestrator import run_moodle_step, run_saga
        from apps.people.models import OutboxEvent
        moodle, mailer, queue = MockMoodleClient(), MockMailerClient(), []
        for event_id in claim_events():
            self.assertEqual(run_saga(event_id, graph=self.graph, moodle=moodle, mailer=mailer,
                                      enqueue_moodle=queue.append), OutboxEvent.Status.WAITING_SUBTASK)
        for event_id in queue:
            self.assertEqual(run_moodle_step(event_id, moodle=moodle), OutboxEvent.Status.PENDING)
        for event_id in claim_events():
            self.assertEqual(run_saga(event_id, graph=self.graph, moodle=moodle, mailer=mailer), OutboxEvent.Status.SUCCEEDED)
        return mailer

    def test_all_global_pages_render_with_shared_navigation(self):
        for name in ('administracion', 'person_create', 'functional_create', 'teams', 'team_create'):
            response = self.client.get(reverse('portal:' + name))
            with self.subTest(route=name):
                self.assertContains(response, 'Alta / Inscripción')
                self.assertContains(response, 'Gestión de Teams')
                self.assertContains(response, 'Cuentas Funcionales')

    def test_manual_alumno_creates_atomic_outbox_redirects_and_provisions_against_mocks(self):
        from apps.platform_mocks.models import MockAzureUser, MockMoodleCourse, MockMoodleEnrollment
        from apps.people.models import OutboxEvent
        for number in (3, 4, 5, 6):
            MockMoodleCourse.objects.create(shortname=f'I{number}', fullname=f'Ingreso {number}')
        response = self.client.post(reverse('portal:person_create'), self.person_data(dni='44.123.456'), follow=True)
        identity = Identity.objects.get(identifier='44123456')
        self.assertEqual(response.redirect_chain, [(reverse('portal:identity360', args=[identity.pk]), 302)])
        self.assertContains(response, 'Alta registrada')
        affiliation = identity.affiliations.get()
        self.assertEqual(affiliation.current_status.code, 'INGRE')
        self.assertEqual(affiliation.history.get().changed_by_user, self.operator)
        self.assertEqual(OutboxEvent.objects.get(identity=identity).status, 'PENDING')
        self.drain()
        self.assertEqual(MockAzureUser.objects.get().upn, 'a44123456@eco.unrc.edu.ar')
        self.assertEqual(MockAzureUser.objects.get().assigned_sku, 'mock-a1')
        self.assertEqual(MockMoodleEnrollment.objects.count(), 4)

    def test_staff_initial_profile_emits_provision_and_htmx_redirect(self):
        from apps.people.models import OutboxEvent
        response = self.client.post(reverse('portal:person_create'), self.person_data(role='STAFF', reference='Secretaría'), HTTP_HX_REQUEST='true')
        identity = Identity.objects.get(identifier='44123456')
        self.assertEqual(response['HX-Redirect'], reverse('portal:identity360', args=[identity.pk]))
        affiliation = identity.affiliations.get()
        self.assertEqual(affiliation.profile_type.code, 'STAFF')
        self.assertEqual(affiliation.current_status.code, 'ACTIVO')
        self.assertTrue(OutboxEvent.objects.get().payload['requires_m365_license'])
        self.drain()

    def test_functional_account_has_no_dni_or_surname_and_completes_graph_only(self):
        from apps.platform_mocks.models import MockAzureUser, MockMoodleUser
        from apps.people.models import OutboxEvent
        response = self.client.post(reverse('portal:functional_create'), self.functional_data(), follow=True)
        self.assertContains(response, 'Alta registrada')
        identity = Identity.objects.get(identifier='CATEDRA_ECO')
        self.assertEqual(identity.category, 'ORGANIZACIONAL')
        self.assertEqual(identity.last_name, '')
        self.assertFalse(identity.identifier.isnumeric())
        self.assertEqual(identity.affiliations.get().profile_type.code, 'INSTITUCIONAL')
        self.assertEqual(OutboxEvent.objects.get().payload['identity_category'], 'ORGANIZACIONAL')
        mailer = self.drain()
        azure = MockAzureUser.objects.get()
        azure.full_clean()
        self.assertEqual(azure.upn, 'catedra.eco@eco.unrc.edu.ar')
        self.assertEqual(azure.document_number, '')
        self.assertIsNone(azure.assigned_sku)
        self.assertEqual(MockMoodleUser.objects.count(), 0)
        self.assertEqual(mailer.deliveries, [('responsable@example.test', azure.upn)])

    def test_invalid_and_duplicate_manual_posts_do_not_duplicate_or_partially_create(self):
        from apps.people.models import OutboxEvent
        self.client.post(reverse('portal:person_create'), self.person_data(role='STAFF'))
        response = self.client.post(reverse('portal:person_create'), self.person_data(role='STAFF'), HTTP_HX_REQUEST='true')
        self.assertContains(response, 'La identidad ya existe')
        self.assertEqual(Identity.objects.count(), 1)
        self.assertEqual(OutboxEvent.objects.count(), 1)
        response = self.client.post(reverse('portal:functional_create'), self.functional_data(institutional_upn='unidad@gmail.com'), HTTP_HX_REQUEST='true')
        self.assertContains(response, 'El UPN debe pertenecer')
        self.assertEqual(Identity.objects.count(), 1)

    def test_manual_creation_rolls_back_identity_and_outbox_when_domain_fails(self):
        from django.core.exceptions import ValidationError
        from apps.people.models import OutboxEvent
        with patch('apps.portal.global_services.create_affiliation', side_effect=ValidationError('Fallo de dominio')):
            response = self.client.post(reverse('portal:person_create'), self.person_data(), HTTP_HX_REQUEST='true')
        self.assertContains(response, 'Fallo de dominio')
        self.assertEqual(Identity.objects.count(), 0)
        self.assertEqual(PersonalProfile.objects.count(), 0)
        self.assertEqual(OutboxEvent.objects.count(), 0)

    def test_team_creation_persists_mock_owner_and_audits_operator(self):
        from apps.platform_mocks.models import MockTeamMember
        team = self.create_team()
        self.assertEqual(team.display_name, 'Cátedra Economía')
        self.assertEqual(team.owner.upn, 'docente@eco.unrc.edu.ar')
        self.assertTrue(MockTeamMember.objects.get(team=team).is_owner)
        response = self.client.get(reverse('portal:teams'))
        self.assertContains(response, str(team.pk))
        self.assertContains(response, 'SUCCEEDED')
        self.assertTrue(LogEntry.objects.filter(user=self.operator, object_id=str(team.pk), change_message__contains='create_team').exists())

    def test_team_purge_retains_all_owners_and_ignores_untrusted_policy_input(self):
        from apps.platform_mocks.models import MockTeamMember
        team = self.create_team()
        second_owner = self.team_owner('otro.docente@eco.unrc.edu.ar')
        student = self.team_owner('alumno@eco.unrc.edu.ar')
        MockTeamMember.objects.create(team=team, user=second_owner, is_owner=True)
        MockTeamMember.objects.create(team=team, user=student, is_owner=False)
        response = self.client.post(reverse('portal:team_purge', args=[team.pk]),
            {'confirm': 'on', 'keep_owners': 'false'}, HTTP_HX_REQUEST='true')
        self.assertContains(response, '1 miembros retirados')
        self.assertEqual(set(team.members.values_list('user_id', flat=True)), {team.owner_id, second_owner.pk})
        self.assertNotContains(response, '<html')

    def test_team_purge_requires_confirmation_and_valid_uuid(self):
        from apps.platform_mocks.models import MockTeamMember
        team = self.create_team()
        student = self.team_owner('alumno@eco.unrc.edu.ar')
        MockTeamMember.objects.create(team=team, user=student)
        response = self.client.post(reverse('portal:team_purge', args=[team.pk]), {}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'confirme la operación')
        self.assertEqual(team.members.count(), 2)
        response = self.client.post(reverse('portal:team_purge_form'), {'team_id': 'no-uuid', 'confirm': 'on'}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'confirme la operación')
        self.assertEqual(team.members.count(), 2)

    def test_missing_mock_owner_is_an_error_without_team_creation(self):
        from apps.platform_mocks.models import MockTeam
        response = self.client.post(reverse('portal:team_create'), {
            'display_name': 'Sin propietario', 'description': '', 'owner_upn': 'inexistente@eco.unrc.edu.ar',
        }, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'El proveedor no completó')
        self.assertEqual(MockTeam.objects.count(), 0)
        self.assertTrue(LogEntry.objects.filter(change_message__contains='FAILED').exists())

    def test_global_routes_reject_anonymous_and_read_only_staff(self):
        self.client.logout()
        for route in ('person_create', 'functional_create', 'teams', 'team_create'):
            self.assertEqual(self.client.get(reverse('portal:' + route)).status_code, 302)
        reader = get_user_model().objects.create_user('global-reader', is_staff=True)
        reader.user_permissions.add(Permission.objects.get(codename='view_identity'))
        self.client.force_login(reader)
        for route in ('person_create', 'functional_create', 'teams', 'team_create'):
            self.assertEqual(self.client.get(reverse('portal:' + route)).status_code, 403)
        self.assertEqual(self.client.post(reverse('portal:team_purge_form'), {}).status_code, 403)

    def test_global_posts_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.operator)
        for route in ('person_create', 'functional_create', 'team_create', 'team_purge_form'):
            self.assertEqual(client.post(reverse('portal:' + route), {}).status_code, 403)


@override_settings(LUCY_DRY_RUN=True, LUCY_M365_SKU_ID='role-test-a1')
class AdministrationRoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {}
        for role in ('jefe', 'operador', 'admin'):
            user = get_user_model().objects.create_user(f'rol-{role}', password='test')
            user.groups.add(Group.objects.get(name=f'adm_alumnos_rol_{role}'))
            cls.users[role] = user
        cls.identity = Identity.objects.create(
            category='FISICA', identifier='55123456', first_name='Lucía', last_name='Gómez',
            institutional_upn='a55123456@eco.unrc.edu.ar', metadata={'legajo': 'ROLE-123'},
        )
        PersonalProfile.objects.create(identity=cls.identity, personal_email='lucia@example.test',
            phone='35812345', city='Río Cuarto', address='Domicilio de prueba')
        profile = ProfileTypeDefinition.objects.get(code='ALUMNO')
        cls.pre = SagaStatusDefinition.objects.get(profile_type=profile, code='PRE')
        cls.asp = SagaStatusDefinition.objects.get(profile_type=profile, code='ASP')
        cls.affiliation = Affiliation.objects.create(identity=cls.identity, profile_type=profile, current_status=cls.pre, reference='2')
        cls.rule = SagaTransitionRule.objects.get(profile_type=profile, from_status=cls.pre, to_status=cls.asp)
        from apps.people.models import DigitalAccount
        DigitalAccount.objects.create(identity=cls.identity, platform='TEAMS', account_identifier=cls.identity.institutional_upn)
        AffiliationStatusHistory.objects.create(affiliation=cls.affiliation, to_status=cls.pre,
            changed_by='test:alta', reason='Antecedente institucional')

    def login(self, role):
        self.client.force_login(self.users[role])

    def url(self, name, **extra):
        return reverse('portal:' + name, kwargs={'identity_id': self.identity.pk, **extra})

    def post(self, name, data=None, **extra):
        return self.client.post(self.url(name, **extra), data or {}, HTTP_HX_REQUEST='true')

    def contact_data(self):
        return {'first_name': 'Lucía María', 'last_name': 'Gómez', 'personal_email': 'lucia@example.test',
                'phone': '3589999', 'city': 'Río Cuarto', 'address': 'Nuevo domicilio'}

    def test_group_seed_has_exact_cumulative_permissions_and_is_idempotent(self):
        from importlib import import_module
        from django.db import connection
        seed = import_module('apps.people.migrations.0013_seed_student_administration_roles').seed_administration_roles
        seed(apps, type('SchemaEditor', (), {'connection': connection})())
        expected = {'view_identity', 'view_affiliation', 'view_personalprofile', 'can_view_student_360'}
        for role, extra in (('jefe', None), ('operador', 'can_operate_student_daily'), ('admin', 'can_administrate_student_critical')):
            if extra:
                expected.add(extra)
            group = Group.objects.get(name=f'adm_alumnos_rol_{role}')
            self.assertEqual(set(group.permissions.values_list('codename', flat=True)), expected)
            self.assertTrue(self.users[role].groups.filter(pk=group.pk).exists())
        self.assertEqual(Group.objects.filter(name__startswith='adm_alumnos_rol_').count(), 3)

    def test_jefe_reads_complete_360_and_search_but_has_no_action_forms(self):
        self.login('jefe')
        response = self.client.get(self.url('identity360'), HTTP_HX_REQUEST='true')
        for text in ('ROLE-123', 'lucia@example.test', '35812345', 'Río Cuarto', 'Domicilio de prueba',
                     'Microsoft Teams', 'Antecedente institucional'):
            self.assertContains(response, text)
        self.assertNotContains(response, '<form')
        self.assertNotContains(response, '<button')
        self.assertNotContains(response, '<select')
        response = self.client.get(reverse('portal:administracion_buscar'), {'q': '55123456'}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'Abrir Ficha 360°')

    def test_jefe_gets_403_for_all_daily_and_critical_endpoints(self):
        self.login('jefe')
        with patch('apps.people.support.build_graph') as graph:
            for name in ('identity_contact', 'identity_metadata', 'identity_credentials', 'identity_sessions',
                         'identity_license', 'identity_suspend', 'identity_moodle_enrol', 'identity_moodle_unenrol'):
                with self.subTest(endpoint=name):
                    self.assertEqual(self.post(name).status_code, 403)
            for name in ('affiliation_transition', 'affiliation_ttl'):
                self.assertEqual(self.post(name, affiliation_id=self.affiliation.pk).status_code, 403)
            graph.assert_not_called()
        for name in ('person_create', 'functional_create', 'team_create', 'team_purge_form'):
            self.assertEqual(self.client.post(reverse('portal:' + name), {}).status_code, 403)

    def test_operador_executes_daily_contact_credentials_sessions_and_moodle(self):
        self.login('operador')
        with patch('requests.sessions.Session.request', side_effect=AssertionError('Red prohibida')):
            self.assertContains(self.post('identity_contact', self.contact_data()), 'Datos de contacto actualizados')
            self.assertContains(self.post('identity_credentials'), 'SIMULACIÓN')
            self.assertContains(self.post('identity_sessions'), 'SIMULACIÓN')
            for name in ('identity_moodle_enrol', 'identity_moodle_unenrol'):
                self.assertContains(self.post(name, {'shortname': 'I1', 'role_id': 5}), 'SIMULACIÓN')
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.first_name, 'Lucía María')
        self.assertTrue(LogEntry.objects.filter(user=self.users['operador'], change_message__contains='SUCCEEDED').exists())

    def test_operador_cannot_call_critical_endpoints_even_with_native_crud_permissions(self):
        self.users['operador'].user_permissions.add(*Permission.objects.filter(
            content_type__app_label='people', codename__in=('change_identity', 'change_affiliation', 'add_identity', 'add_affiliation'),
        ))
        self.login('operador')
        for name in ('identity_license', 'identity_suspend'):
            self.assertEqual(self.post(name).status_code, 403)
        for name in ('affiliation_transition', 'affiliation_ttl'):
            self.assertEqual(self.post(name, affiliation_id=self.affiliation.pk).status_code, 403)
        for name in ('person_create', 'functional_create', 'team_create', 'team_purge_form'):
            self.assertEqual(self.client.post(reverse('portal:' + name), {}).status_code, 403)
        self.assertEqual(self.client.get(reverse('portal:teams')).status_code, 403)

    def test_operador_html_exposes_only_daily_tools_and_hides_critical_navigation(self):
        self.login('operador')
        response = self.client.get(self.url('identity360'), HTTP_HX_REQUEST='true')
        for name in ('identity_contact', 'identity_credentials', 'identity_sessions', 'identity_moodle_enrol', 'identity_moodle_unenrol'):
            self.assertContains(response, self.url(name))
        for name in ('identity_license', 'identity_suspend'):
            self.assertNotContains(response, self.url(name))
        self.assertNotContains(response, self.url('affiliation_transition', affiliation_id=self.affiliation.pk))
        self.assertNotContains(response, self.url('affiliation_ttl', affiliation_id=self.affiliation.pk))
        response = self.client.get(self.url('identity360'))
        for name in ('person_create', 'functional_create', 'teams'):
            self.assertNotContains(response, reverse('portal:' + name))

    def test_admin_without_native_crud_executes_critical_and_daily_operations(self):
        from apps.people.models import OutboxEvent
        self.login('admin')
        self.assertFalse(self.users['admin'].has_perm('people.change_identity'))
        with patch('requests.sessions.Session.request', side_effect=AssertionError('Red prohibida')):
            self.assertContains(self.post('identity_contact', self.contact_data()), 'Datos de contacto actualizados')
            self.assertContains(self.post('identity_license'), 'SIMULACIÓN')
            self.assertContains(self.post('identity_suspend'), 'SIMULACIÓN')
            self.assertContains(self.post('affiliation_ttl', {'expires_at': '2029-01-01', 'reason': 'Prórroga'}, affiliation_id=self.affiliation.pk), 'Vigencia actualizada')
            self.assertContains(self.post('affiliation_transition', {'rule': self.rule.pk, 'expected_from_code': 'PRE', 'reason': 'Decisión administrativa'}, affiliation_id=self.affiliation.pk), 'Transición aplicada')
            response = self.client.post(reverse('portal:person_create'), {'dni': '55123457', 'first_name': 'Otra',
                'last_name': 'Persona', 'personal_email': 'otra@example.test', 'reference': 'Área', 'role': 'STAFF'})
            self.assertEqual(response.status_code, 302)
            self.assertTrue(OutboxEvent.objects.filter(identity__identifier='55123457').exists())
            response = self.client.post(reverse('portal:team_create'), {'display_name': 'Cátedra', 'description': '',
                'owner_upn': 'docente@eco.unrc.edu.ar'}, HTTP_HX_REQUEST='true')
            self.assertEqual(response['HX-Redirect'], reverse('portal:teams'))
            response = self.client.post(reverse('portal:team_purge_form'), {
                'team_id': '12345678-1234-4234-9234-123456789012', 'confirm': 'on'}, HTTP_HX_REQUEST='true')
            self.assertContains(response, 'SIMULACIÓN')
        self.assertTrue(self.affiliation.history.filter(changed_by_user=self.users['admin'], rule=self.rule).exists())

    def test_admin_html_has_all_tools_and_navigation(self):
        self.login('admin')
        response = self.client.get(self.url('identity360'))
        for name in ('identity_contact', 'identity_credentials', 'identity_sessions', 'identity_license', 'identity_suspend',
                     'identity_moodle_enrol', 'identity_moodle_unenrol'):
            self.assertContains(response, self.url(name))
        self.assertContains(response, self.url('affiliation_transition', affiliation_id=self.affiliation.pk))
        self.assertContains(response, self.url('affiliation_ttl', affiliation_id=self.affiliation.pk))
        for name in ('person_create', 'functional_create', 'teams'):
            self.assertContains(response, reverse('portal:' + name))

    def test_capabilities_protect_direct_service_calls(self):
        from apps.people.support import perform_support
        from apps.portal.admin_services import update_contact, update_ttl
        from apps.portal.global_services import create_manual_identity, team_operation
        with self.assertRaises(PermissionDenied):
            update_contact(self.identity.pk, self.contact_data(), user=self.users['jefe'])
        with self.assertRaises(PermissionDenied):
            perform_support(self.identity.pk, 'license', user=self.users['operador'])
        with self.assertRaises(PermissionDenied):
            update_ttl(self.affiliation.pk, {}, user=self.users['operador'])
        with self.assertRaises(PermissionDenied):
            create_manual_identity({}, user=self.users['operador'])
        with self.assertRaises(PermissionDenied):
            team_operation('create_team', {}, user=self.users['operador'])

    def test_critical_permission_alone_implies_daily_and_read_in_server_and_html(self):
        user = get_user_model().objects.create_user('critical-only')
        user.user_permissions.add(Permission.objects.get(codename='can_administrate_student_critical'))
        self.client.force_login(user)
        self.assertContains(self.client.get(self.url('identity360')), 'Conmutar Licencia A1')
        self.assertContains(self.post('identity_contact', self.contact_data()), 'Datos de contacto actualizados')

    def test_native_crud_permissions_do_not_grant_portal_capabilities(self):
        user = get_user_model().objects.create_user('legacy-crud', is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='people', codename__in=('view_identity', 'change_identity', 'change_affiliation'),
        ))
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url('identity360')).status_code, 403)
        self.assertEqual(self.post('identity_credentials').status_code, 403)

    def test_portal_login_accepts_active_group_member_without_unfold_staff_access(self):
        response = self.client.post(reverse('portal:administracion_login'), {'username': 'rol-jefe', 'password': 'test'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Buscador / 360°')
        self.assertFalse(self.users['jefe'].is_staff)


@skipUnless(apps.is_installed('apps.platform_mocks'), 'Requiere platform_mocks.')
@override_settings(ENABLE_LOCAL_MOCKS=True, LUCY_DRY_RUN=False, LUCY_M365_SKU_ID='grid-a1')
class StudentGridTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from datetime import timedelta
        from apps.people.models import DigitalAccount, OutboxEvent
        from apps.platform_mocks.models import MockAzureUser, MockMoodleCourse, MockMoodleEnrollment, MockMoodleUser
        cls.operator = get_user_model().objects.create_user('grid-operator')
        cls.operator.groups.add(Group.objects.get(name='adm_alumnos_rol_operador'))
        cls.admin = get_user_model().objects.create_user('grid-admin')
        cls.admin.groups.add(Group.objects.get(name='adm_alumnos_rol_admin'))
        cls.jefe = get_user_model().objects.create_user('grid-jefe')
        cls.jefe.groups.add(Group.objects.get(name='adm_alumnos_rol_jefe'))
        profile = ProfileTypeDefinition.objects.get(code='ALUMNO')
        ingre = SagaStatusDefinition.objects.get(profile_type=profile, code='INGRE')
        pre = SagaStatusDefinition.objects.get(profile_type=profile, code='PRE')
        course = MockMoodleCourse.objects.create(shortname='I1', fullname='Curso de ingreso')
        cls.ids = []
        for index in range(53):
            dni = str(66000000 + index)
            identity = Identity.objects.create(category='FISICA', identifier=dni, first_name='Alumno',
                last_name=f'Apellido{index:03}', institutional_upn=f'a{dni}@eco.unrc.edu.ar')
            cls.ids.append(identity.pk)
            PersonalProfile.objects.create(identity=identity, personal_email=f'alumno{index}@example.test')
            affiliation = Affiliation.objects.create(identity=identity, profile_type=profile,
                current_status=pre if index == 50 else ingre, reference='2' if index < 51 else '7',
                expires_at=timezone.now() + timedelta(days=-1 if index == 2 else 30))
            MockAzureUser.objects.create(upn=identity.institutional_upn, first_name='Alumno', last_name=identity.last_name,
                document_number=dni, assigned_sku=None if index == 5 else 'grid-a1',
                password_profile_force_change=index == 1,
                last_activity_at=None if index == 4 else timezone.now() - timedelta(days=1))
            user = MockMoodleUser.objects.create(username=identity.institutional_upn, email=identity.institutional_upn,
                                                 first_name='Alumno', last_name=identity.last_name)
            if index != 6:
                MockMoodleEnrollment.objects.create(user=user, course=course)
            if index in (3, 6):
                OutboxEvent.objects.create(identity=identity, affiliation=affiliation, action='PROVISION',
                    status='FAILED' if index == 3 else 'RETRY_SCHEDULED', step_results={'moodle': {'status': 'FAILED'}})
        DigitalAccount.objects.create(identity_id=cls.ids[0], platform='TEAMS', account_identifier='a66000000@eco.unrc.edu.ar')

    def setUp(self):
        self.client.force_login(self.operator)
        self.enterContext(patch('apps.portal.bulk_services.current_app.send_task'))

    def grid(self, **params):
        return self.client.get(reverse('portal:administracion'), params, HTTP_HX_REQUEST='true')

    def post_bulk(self, action, ids=None, **options):
        return self.client.post(reverse('portal:student_bulk_action'), {
            'action': action, 'identity_ids': ids or [self.ids[0], self.ids[7]], **options,
        }, HTTP_HX_REQUEST='true')

    def process_support(self):
        from apps.platform_mocks.clients import MockGraphClient, MockMailerClient, MockMoodleClient
        from apps.provisioning.dispatcher import claim_events
        from apps.provisioning.orchestrator import run_saga
        graph, moodle, mailer = MockGraphClient(), MockMoodleClient(), MockMailerClient()
        with patch('apps.people.support.build_graph', return_value=graph), patch('apps.people.support.build_moodle', return_value=moodle), patch('apps.people.support.build_mailer', return_value=mailer):
            ids = claim_events()
            for event_id in ids:
                self.assertEqual(run_saga(event_id, dry_run=False), 'SUCCEEDED')
                self.assertEqual(run_saga(event_id, dry_run=False), 'SUCCEEDED')
        return mailer

    def test_default_grid_is_paginated_50_without_search_and_only_students(self):
        Identity.objects.create(category='ORGANIZACIONAL', identifier='NO_ALUMNO', first_name='Unidad')
        response = self.grid()
        self.assertEqual(response.context['page_obj'].paginator.count, 53)
        self.assertEqual(len(response.context['identities']), 50)
        self.assertContains(response, 'id="student-grid"')
        self.assertContains(response, 'id="tabla-alumnos-container"')
        second = self.grid(page=2)
        self.assertEqual(len(second.context['identities']), 3)
        self.assertFalse({i.pk for i in response.context['identities']} & {i.pk for i in second.context['identities']})

    def test_career_status_search_and_health_filters_combine_before_pagination(self):
        self.assertEqual(self.grid(career='7').context['page_obj'].paginator.count, 2)
        self.assertEqual(self.grid(career='2', status='PRE').context['page_obj'].paginator.count, 1)
        self.assertEqual(self.grid(career='7', status='PRE').context['page_obj'].paginator.count, 0)
        response = self.grid(health='green')
        self.assertEqual(response.context['page_obj'].paginator.count, 47)
        self.assertIn(self.ids[52], [i.pk for i in response.context['identities']])
        self.assertEqual(self.grid(career='7', q='a66000052@eco.unrc.edu.ar').context['identities'][0].pk, self.ids[52])

    def test_health_precedence_and_missing_observations(self):
        from apps.people.models import OutboxEvent
        response = self.grid()
        health = {identity.pk: identity.health for identity in response.context['identities']}
        expected = {0: 'green', 1: 'yellow', 2: 'orange', 3: 'red', 4: 'yellow', 5: 'orange', 6: 'red'}
        for index, color in expected.items():
            self.assertEqual(health[self.ids[index]], color)
        OutboxEvent.objects.create(identity_id=self.ids[2], affiliation=Identity.objects.get(pk=self.ids[2]).affiliations.get(), action='PROVISION', status='FAILED')
        self.assertEqual(self.grid(q='66000002').context['identities'][0].health, 'red')

    @override_settings(ENABLE_LOCAL_MOCKS=False)
    def test_real_mode_diagnosis_uses_local_digital_account_observations(self):
        from apps.people.models import DigitalAccount
        official = DigitalAccount.objects.get(identity_id=self.ids[0], platform='TEAMS')
        official.observations = {'license_assigned': True, 'force_password_change': False,
                                 'last_activity_at': timezone.now().isoformat()}
        official.save()
        DigitalAccount.objects.create(identity_id=self.ids[0], platform='MOODLE', account_identifier='moodle-66000000', observations={'courses': ['I1']})
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No leer APIs en la grilla')):
            self.assertEqual(self.grid(q='66000000').context['identities'][0].health, 'green')
        official.observations.pop('last_activity_at')
        official.save()
        self.assertEqual(self.grid(q='66000000').context['identities'][0].health, 'yellow')

    def test_query_count_is_constant_for_one_or_fifty_rows_and_render_has_no_queries(self):
        from django.template.loader import render_to_string
        from apps.portal.student_grid import student_grid_context
        with self.assertNumQueries(7):
            student_grid_context({'q': '66000000'})
        with self.assertNumQueries(7):
            context = student_grid_context({})
        with self.assertNumQueries(7):
            context = student_grid_context({'page_size': '100'})
        with self.assertNumQueries(0):
            render_to_string('portal/fragments/administracion_results.html', context)

    def test_bulk_credentials_persist_actor_and_batch_then_process_once(self):
        from apps.people.models import OutboxEvent
        response = self.post_bulk('credentials')
        self.assertContains(response, '2 solicitudes registradas en el Outbox')
        events = list(OutboxEvent.objects.filter(action='SUPPORT'))
        self.assertEqual(len(events), 2)
        self.assertEqual(len({event.payload['batch_id'] for event in events}), 1)
        self.assertTrue(all(event.requested_by_id == self.operator.pk and event.status == 'PENDING' for event in events))
        self.assertEqual(len(self.process_support().deliveries), 2)
        self.assertEqual(OutboxEvent.objects.filter(action='SUPPORT', status='SUCCEEDED').count(), 2)
        self.assertEqual(self.grid(q='66000000').context['identities'][0].health, 'yellow')

    def test_bulk_moodle_enrolment_creates_events_and_real_mock_enrollments(self):
        from apps.platform_mocks.models import MockMoodleCourse, MockMoodleEnrollment
        MockMoodleCourse.objects.create(shortname='LOTE', fullname='Curso masivo')
        self.assertContains(self.post_bulk('moodle_enrol', shortname='LOTE', role_id=5, group_name='COMISION LOTE'), '2 solicitudes')
        self.process_support()
        self.assertEqual(MockMoodleEnrollment.objects.filter(course__shortname='LOTE', group_name='COMISION LOTE').count(), 2)

    def test_operator_cannot_toggle_bulk_licenses_but_admin_can(self):
        from apps.people.models import OutboxEvent
        from apps.platform_mocks.models import MockAzureUser
        self.assertEqual(self.post_bulk('license').status_code, 403)
        self.assertFalse(OutboxEvent.objects.filter(action='SUPPORT').exists())
        self.assertNotContains(self.grid(), 'Conmutar Licencia A1 en Lote')
        self.client.force_login(self.admin)
        self.assertContains(self.grid(), 'Conmutar Licencia A1 en Lote')
        self.assertContains(self.post_bulk('license'), '2 solicitudes')
        self.process_support()
        self.assertIsNone(MockAzureUser.objects.get(upn='a66000000@eco.unrc.edu.ar').assigned_sku)

    def test_bulk_selection_is_atomic_deduplicated_bounded_and_student_only(self):
        from apps.people.models import OutboxEvent
        plain = Identity.objects.create(category='ORGANIZACIONAL', identifier='UNIDAD', first_name='Unidad')
        response = self.post_bulk('credentials', ids=[self.ids[0], plain.pk])
        self.assertContains(response, 'no son alumnos')
        self.assertFalse(OutboxEvent.objects.filter(action='SUPPORT').exists())
        self.assertContains(self.post_bulk('credentials', ids=list(range(1, 202))), 'entre 1 y 200')
        self.assertContains(self.post_bulk('credentials', ids=[self.ids[0], self.ids[0]]), '1 solicitudes')
        self.assertEqual(OutboxEvent.objects.filter(action='SUPPORT').count(), 1)

    def test_revoked_permissions_fail_queued_command_without_external_calls(self):
        from apps.people.models import OutboxEvent
        from apps.provisioning.dispatcher import claim_events
        from apps.provisioning.orchestrator import run_saga
        self.post_bulk('credentials', ids=[self.ids[0]])
        self.operator.groups.clear()
        event_id = claim_events()[0]
        with patch('apps.people.support.build_graph') as factory:
            self.assertEqual(run_saga(event_id), 'FAILED')
            factory.assert_not_called()
        self.assertEqual(OutboxEvent.objects.get(pk=event_id).last_error.split(':')[0], 'PermissionDenied')

    def test_jefe_sees_grid_without_bulk_controls_and_bulk_post_is_403(self):
        self.client.force_login(self.jefe)
        self.assertNotContains(self.grid(), 'student-bulk-form')
        self.assertEqual(self.post_bulk('credentials').status_code, 403)

    def test_bulk_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.operator)
        self.assertEqual(client.post(reverse('portal:student_bulk_action'), {
            'action': 'credentials', 'identity_ids': [self.ids[0]]}).status_code, 403)

    def test_support_outbox_dry_run_override_never_resets_real_or_mock_credentials(self):
        from apps.platform_mocks.models import MockAzureUser
        from apps.provisioning.dispatcher import claim_events
        from apps.provisioning.orchestrator import run_saga
        self.post_bulk('credentials', ids=[self.ids[0]])
        event_id = claim_events()[0]
        with patch('requests.sessions.Session.request', side_effect=AssertionError('Red prohibida')), patch('smtplib.SMTP') as smtp:
            self.assertEqual(run_saga(event_id, dry_run=True), 'SIMULATED')
            smtp.assert_not_called()
        self.assertFalse(MockAzureUser.objects.get(upn='a66000000@eco.unrc.edu.ar').password_profile_force_change)


    def test_page_sizes_preserve_filters_and_validate_choices(self):
        for size in (25, 100):
            response = self.grid(page_size=size, career='2', q='Alumno')
            self.assertEqual(response.context['page_obj'].paginator.per_page, size)
            self.assertEqual(len(response.context['identities']), min(size, 51))
            if size == 25:
                self.assertIn('page_size=25', response.context['next_url'])
                self.assertIn('career=2', response.context['next_url'])
                self.assertIn('q=Alumno', response.context['next_url'])
        for invalid in ('0', '999', 'texto'):
            self.assertEqual(self.grid(page_size=invalid).context['page_obj'].paginator.per_page, 50)
        response = self.client.get(reverse('portal:administracion'), {'page_size': 100})
        self.assertContains(response, 'name="page_size"')
        self.assertContains(response, 'value="100" selected')
        self.assertContains(response, 'hx-push-url="true"')

    def test_excel_has_same_filters_all_pages_and_read_permission(self):
        from io import BytesIO
        from openpyxl import load_workbook
        url = reverse('portal:student_export')
        params = {'career': '7', 'status': 'INGRE', 'health': 'green', 'q': 'Apellido', 'page_size': 25}
        response = self.client.get(url, params)
        self.assertEqual(response['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('attachment;', response['Content-Disposition'])
        book = load_workbook(BytesIO(b''.join(response.streaming_content)))
        rows = list(book.active.values)
        self.assertEqual(rows[0][1:4], ('DNI', 'Apellido y Nombre', 'UPN Institucional'))
        self.assertEqual([row[1] for row in rows[1:]], ['66000051', '66000052'])
        self.assertTrue(all(row[4:6] == ('7', 'INGRE') for row in rows[1:]))
        response = self.client.get(url, {'page_size': 25, 'page': 2})
        self.assertEqual(len(list(load_workbook(BytesIO(b''.join(response.streaming_content))).active.values)), 54)
        self.client.force_login(self.jefe)
        self.assertEqual(self.client.get(url).status_code, 200)
        denied = get_user_model().objects.create_user('sin-lectura-excel')
        self.client.force_login(denied)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_notification_outbox_substitutes_variables_and_dispatches_mail(self):
        from apps.people.models import OutboxEvent
        response = self.post_bulk('notification', subject='Aviso {document_number}',
                                  body='Hola {full_name}, tu cuenta es {upn}. DNI: {document_number}.')
        self.assertContains(response, '2 solicitudes registradas')
        events = list(OutboxEvent.objects.filter(action='SUPPORT').order_by('identity_id'))
        self.assertEqual(len(events), 2)
        for event in events:
            identity = event.identity
            options = event.payload['support_options']
            self.assertEqual(event.requested_by_id, self.operator.pk)
            self.assertEqual(options['subject'], f'Aviso {identity.identifier}')
            self.assertEqual(options['recipient'], identity.institutional_upn)
            self.assertEqual(options['body'], f'Hola {identity.first_name} {identity.last_name}, tu cuenta es {identity.institutional_upn}. DNI: {identity.identifier}.')
        self.assertEqual(len(self.process_support().deliveries), 2)

    def test_notifications_reject_invalid_templates_and_roll_back_whole_batch(self):
        from apps.people.models import OutboxEvent
        for subject, body in (('', 'Mensaje'), ('Aviso', '{unknown}'), ('Aviso', '{upn.__class__}'),
                              ('Aviso', '{upn:>100}'), ('Aviso', '{'), ('Aviso\nBcc: otro', 'Mensaje')):
            self.post_bulk('notification', subject=subject, body=body)
            self.assertFalse(OutboxEvent.objects.filter(action='SUPPORT').exists())
        Identity.objects.filter(pk=self.ids[7]).update(institutional_upn=None)
        self.post_bulk('notification', subject='Aviso', body='Hola {full_name}')
        self.assertFalse(OutboxEvent.objects.filter(action='SUPPORT').exists())
        self.client.force_login(self.jefe)
        self.assertEqual(self.post_bulk('notification', subject='Aviso', body='Mensaje').status_code, 403)

    def test_seed_command_is_idempotent_and_matches_health_distribution(self):
        from collections import Counter
        from io import StringIO
        from django.core.management import call_command
        from apps.people.models import OutboxEvent
        from apps.platform_mocks.models import MockAzureUser, MockMoodleEnrollment
        from apps.portal.student_grid import student_queryset
        output = StringIO()
        for _ in range(2):
            call_command('seed_dev_students', count=60, stdout=output)
        students = Identity.objects.filter(metadata__seed='seed_dev_students')
        self.assertEqual(students.count(), 60)
        self.assertEqual(Affiliation.objects.filter(identity__in=students).count(), 60)
        self.assertEqual(MockAzureUser.objects.filter(upn__in=students.values('institutional_upn')).count(), 60)
        self.assertEqual(MockMoodleEnrollment.objects.filter(course__shortname__in=['9040', '9041', '9042'], user__username__startswith='a95').count(), 60)
        self.assertEqual(OutboxEvent.objects.filter(payload={'seed': 'seed_dev_students'}).count(), 60)
        self.assertEqual(Counter(student_queryset({}).filter(pk__in=students).values_list('health', flat=True)),
                         {'green': 30, 'yellow': 12, 'orange': 12, 'red': 6})
        self.assertIn('Semilla sincronizada: 60 alumnos', output.getvalue())

    @override_settings(LUCY_M365_SKU_ID='')
    def test_seed_uses_shared_mock_sku_without_configuration(self):
        from io import StringIO
        from django.core.management import call_command
        from apps.portal.student_grid import student_queryset
        call_command('seed_dev_students', count=10, stdout=StringIO())
        self.assertEqual(student_queryset({'health': 'green'}).filter(metadata__seed='seed_dev_students').count(), 5)

    def test_excel_keeps_untrusted_values_as_text(self):
        from io import BytesIO
        from openpyxl import load_workbook
        Identity.objects.filter(pk=self.ids[0]).update(last_name='=1+1')
        response = self.client.get(reverse('portal:student_export'), {'q': '66000000'})
        sheet = load_workbook(BytesIO(b''.join(response.streaming_content))).active
        self.assertEqual(sheet['C2'].data_type, 's')
        self.assertEqual(sheet['C2'].value, '=1+1, Alumno')

    def test_failed_notification_is_retried_without_claiming_success(self):
        from apps.people.models import OutboxEvent
        from apps.provisioning.dispatcher import claim_events
        from apps.provisioning.orchestrator import run_saga
        self.post_bulk('notification', ids=[self.ids[0]], subject='Aviso', body='Mensaje')
        event_id = claim_events()[0]
        mailer = Mock(dry_run=False)
        mailer.send_email.return_value = MailDeliveryResult('alumno@example.test', 'Aviso', False)
        with patch('apps.people.support.build_mailer', return_value=mailer):
            self.assertEqual(run_saga(event_id, dry_run=False), 'RETRY_SCHEDULED')
        self.assertEqual(OutboxEvent.objects.get(pk=event_id).status, 'RETRY_SCHEDULED')

    def test_seed_invalid_count_and_disabled_mocks_do_not_write(self):
        from django.core.management import call_command, CommandError
        for count in (0, -1, 10001):
            with self.assertRaises(CommandError):
                call_command('seed_dev_students', count=count)
        with self.settings(ENABLE_LOCAL_MOCKS=False), self.assertRaises(CommandError):
            call_command('seed_dev_students', count=60)
        self.assertFalse(Identity.objects.filter(metadata__seed='seed_dev_students').exists())


    def test_formula_parser_compiles_without_queries_and_combines_conditions(self):
        from apps.portal.query_parser import parse_query
        from apps.portal.student_grid import student_queryset
        from apps.people.models import OutboxEvent
        with self.assertNumQueries(0):
            criteria = parse_query('carrera:CONTADOR AND salud:ROJO AND vence:vencido')
        self.assertTrue(criteria.identity.children)
        self.assertTrue(criteria.affiliation.children)
        affiliation = Affiliation.objects.get(identity_id=self.ids[2])
        affiliation.reference = 'Contador Público'
        affiliation.save()
        OutboxEvent.objects.create(identity_id=self.ids[2], affiliation=affiliation, action='PROVISION', status='FAILED')
        result = student_queryset({'formula': 'carrera:CONTADOR AND salud:ROJO AND vence:vencido'})
        self.assertEqual(list(result.values_list('pk', flat=True)), [self.ids[2]])
        self.assertEqual(list(student_queryset({'formula': 'carrera:"Contador Público" AND saga:INGRE'}).values_list('pk', flat=True)), [self.ids[2]])

    def test_formula_career_saga_and_ttl_must_match_same_affiliation(self):
        from datetime import timedelta
        from apps.portal.student_grid import student_queryset
        profile = ProfileTypeDefinition.objects.get(code='ALUMNO')
        pre = SagaStatusDefinition.objects.get(profile_type=profile, code='PRE')
        Affiliation.objects.create(identity_id=self.ids[0], profile_type=profile, current_status=pre,
                                   reference='Contador', expires_at=timezone.now() - timedelta(days=1))
        for formula in ('carrera:Contador AND saga:INGRE', 'carrera:2 AND vence:vencido'):
            self.assertFalse(student_queryset({'formula': formula, 'q': '66000000'}).exists())
        self.assertFalse(student_queryset({'formula': 'saga:PRE', 'career': '2', 'q': '66000000'}).exists())

    def test_formula_boolean_dni_and_ttl_window_semantics(self):
        from datetime import timedelta
        from apps.portal.student_grid import student_queryset
        now = timezone.now()
        Affiliation.objects.update(expires_at=now + timedelta(days=45))
        Affiliation.objects.filter(identity_id=self.ids[0]).update(expires_at=now + timedelta(days=5))
        params = {'formula': 'dni:6600000 AND licencia:SI AND moodle:SI AND vence:<30d'}
        self.assertEqual(list(student_queryset(params, now=now).values_list('pk', flat=True)), [self.ids[0]])
        self.assertEqual(list(student_queryset({'formula': 'dni:66000005 AND licencia:NO'}).values_list('pk', flat=True)), [self.ids[5]])
        self.assertEqual(list(student_queryset({'formula': 'dni:66000006 AND moodle:NO'}).values_list('pk', flat=True)), [self.ids[6]])
        Affiliation.objects.filter(identity_id=self.ids[0]).update(expires_at=None)
        self.assertFalse(student_queryset(params, now=now).exists())

    def test_formula_invalid_keys_syntax_and_injection_are_controlled(self):
        from apps.portal.query_parser import QuerySyntaxError, parse_query
        invalid = ('password:secreto', 'identity__password:x', 'dni:1 OR dni:2',
                   'dni:1 AND', 'AND dni:1', 'dni:', 'carrera:"sin cierre',
                   'dni:1;DROP', 'salud:AZUL', 'licencia:TRUE', 'moodle:TALVEZ',
                   'vence:<0d', 'vence:<9999d', 'vence:mañana', 'saga:PRE;DROP',
                   'dni:1 AND ' * 31 + 'dni:1', 'x' * 2001)
        for expression in invalid:
            with self.subTest(expression=expression), self.assertRaises(QuerySyntaxError):
                parse_query(expression)
            response = self.grid(formula=expression)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'role="alert"')
            self.assertEqual(response.context['page_obj'].paginator.count, 0)
        response = self.client.get(reverse('portal:student_export'), {'formula': 'password:secreto'})
        self.assertEqual(response.status_code, 400)

    def test_formula_preserves_seven_queries_and_zero_during_render(self):
        from django.template.loader import render_to_string
        from apps.portal.student_grid import student_grid_context
        from apps.people.permissions import ADMINISTRATE_STUDENT_CRITICAL, has_portal_permission
        has_portal_permission(self.operator, ADMINISTRATE_STUDENT_CRITICAL)
        for expression in ('dni:66000000', 'dni:6600 AND licencia:SI', ''):
            with self.assertNumQueries(7):
                context = student_grid_context({'formula': expression, 'page_size': '100'}, user=self.operator)
            with self.assertNumQueries(0):
                render_to_string('portal/fragments/administracion_results.html', context)

    def test_snippet_create_apply_htmx_and_private_shared_isolation(self):
        from apps.portal.models import AdminQuerySnippet
        create = reverse('portal:snippet_create')
        response = self.client.post(create, {'snippet_name': 'Mis ingresantes', 'formula': 'saga:INGRE',
                                            'career': '2', 'page_size': '25'}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'Vista guardada: Mis ingresantes.')
        private = AdminQuerySnippet.objects.get(name='Mis ingresantes')
        self.assertEqual(private.created_by_id, self.operator.pk)
        self.assertEqual(private.filter_params, {'career': '2', 'page_size': '25'})
        self.assertFalse(private.is_shared)
        self.client.post(create, {'snippet_name': 'Compartida de soporte', 'formula': 'salud:ROJO', 'is_shared': 'on'})
        shared = AdminQuerySnippet.objects.get(name='Compartida de soporte')
        other = get_user_model().objects.create_user('operador-aislado')
        other.groups.add(Group.objects.get(name='adm_alumnos_rol_operador'))
        self.client.force_login(other)
        response = self.client.get(reverse('portal:administracion'))
        self.assertNotContains(response, 'Mis ingresantes')
        self.assertContains(response, 'Compartida de soporte')
        self.assertNotContains(self.grid(snippet=private.pk), 'Mis ingresantes')
        self.assertContains(self.grid(snippet=private.pk), 'no está disponible')
        self.assertEqual(self.grid(snippet=private.pk).context['page_obj'].paginator.count, 0)
        response = self.grid(snippet=shared.pk)
        self.assertEqual(response.context['formula'], 'salud:ROJO')
        self.assertEqual(response.context['page_obj'].paginator.count, 2)
        self.client.force_login(self.operator)
        response = self.grid(snippet=private.pk)
        self.assertNotContains(response, '<html')
        self.assertContains(response, 'hx-swap-oob="outerHTML"')
        self.assertContains(response, 'value="saga:INGRE"')
        self.assertEqual(response.context['page_obj'].paginator.per_page, 25)
        self.assertEqual(response.context['page_obj'].paginator.count, 50)
        self.assertIn('formula=saga%3AINGRE', response.context['next_url'])
        self.assertIn('career=2', response.context['next_url'])

    def test_htmx_only_refreshes_filter_panel_when_applying_snippet(self):
        response = self.grid(q='Alumno', formula='saga:INGRE')
        self.assertNotContains(response, 'hx-swap-oob="outerHTML"')
        self.assertContains(self.grid(snippet=''), 'hx-swap-oob="outerHTML"')

    def test_snippet_delete_requires_owner_or_administrator_and_post_csrf(self):
        from apps.portal.models import AdminQuerySnippet
        snippet = AdminQuerySnippet.objects.create(name='Privada borrable', query_expression='dni:6600', created_by=self.operator)
        url = reverse('portal:snippet_delete', args=[snippet.pk])
        other = get_user_model().objects.create_user('otro-operador-snippets')
        other.groups.add(Group.objects.get(name='adm_alumnos_rol_operador'))
        self.client.force_login(other)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertTrue(AdminQuerySnippet.objects.filter(pk=snippet.pk).exists())
        self.client.force_login(self.jefe)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(self.client.post(reverse('portal:snippet_create'), {'snippet_name': 'Denegado'}).status_code, 403)
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.operator)
        self.assertEqual(csrf.post(url).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 200)
        snippet = AdminQuerySnippet.objects.create(name='Otra privada', created_by=other)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('portal:snippet_delete', args=[snippet.pk])).status_code, 200)

    def test_snippet_rejects_invalid_formula_name_and_persisted_filter_schema(self):
        from apps.portal.models import AdminQuerySnippet
        from apps.portal.snippet_services import create_snippet
        from django.core.exceptions import ValidationError
        for name, expression in (('', 'dni:1'), ('Nombre', 'clave:inyeccion')):
            response = self.client.post(reverse('portal:snippet_create'), {'snippet_name': name, 'formula': expression})
            self.assertContains(response, 'role="status"')
            self.assertFalse(AdminQuerySnippet.objects.exists())
        with self.assertRaises(ValidationError):
            create_snippet(user=self.operator, name='Inseguro', expression='', filter_params={'identity__password': 'x'})
        broken = AdminQuerySnippet.objects.create(name='Esquema inválido', created_by=self.operator, filter_params={'snippet': '1'})
        self.assertContains(self.grid(snippet=broken.pk), 'filtros complementarios inválidos')

    def test_formula_and_snippet_excel_match_grid_and_pagination(self):
        from io import BytesIO
        from openpyxl import load_workbook
        from apps.portal.models import AdminQuerySnippet
        snippet = AdminQuerySnippet.objects.create(name='Errores', created_by=self.operator,
            query_expression='salud:ROJO AND saga:INGRE', filter_params={'career': '2', 'page_size': '25'})
        expected = [identity.identifier for identity in self.grid(snippet=snippet.pk).context['identities']]
        response = self.client.get(reverse('portal:student_export'), {'snippet': snippet.pk})
        rows = list(load_workbook(BytesIO(b''.join(response.streaming_content))).active.values)
        self.assertEqual([row[1] for row in rows[1:]], expected)
        self.assertEqual(len(expected), 2)


    def test_main_screen_is_compact_builder_route_and_numbered_deterministic_pages(self):
        response = self.client.get(reverse('portal:administracion'), {'page_size': 25})
        self.assertNotContains(response, 'Filtro Avanzado / Fórmula')
        self.assertNotContains(response, 'Guardar como Snippet')
        self.assertNotContains(response, 'Administrar vistas guardadas')
        content = response.content.decode()
        self.assertGreater(content.index('Vistas Guardadas (Snippets)'), content.index('aria-label="Paginación de alumnos"'))
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, 'page_size=25&amp;page=3')
        ordered = list(Identity.objects.filter(pk__in=self.ids).order_by('last_name', 'first_name', 'id').values_list('pk', flat=True))
        first = self.grid(page_size=25, q='Alumno', career='2')
        second = self.grid(page_size=25, q='Alumno', career='2', page=2)
        self.assertEqual([i.pk for i in first.context['identities']], ordered[:25])
        self.assertFalse({i.pk for i in first.context['identities']} & {i.pk for i in second.context['identities']})
        self.assertIn('career=2', first.context['next_url'])
        builder = self.client.get(reverse('portal:query_builder'), {'formula': 'saga:INGRE'})
        self.assertContains(builder, 'Filtro Avanzado / Fórmula')
        self.assertContains(builder, 'Vista previa de alumnos')
        self.assertContains(builder, 'Guardar como Snippet')
        self.assertEqual(builder.context['page_obj'].paginator.count, 52)
        fragment = self.client.get(reverse('portal:query_builder'), {'formula': 'saga:PRE'}, HTTP_HX_REQUEST='true')
        self.assertContains(fragment, 'id="query-builder"')
        self.assertNotContains(fragment, '<html')
        saved = self.client.post(reverse('portal:snippet_create'), {'builder_mode': '1', 'snippet_name': 'Vista del constructor', 'formula': 'saga:PRE'}, HTTP_HX_REQUEST='true')
        self.assertContains(saved, 'id="query-builder"')
        self.assertContains(saved, 'Vista guardada: Vista del constructor')

    def test_elided_page_range_and_id_tiebreaker_are_deterministic(self):
        profile = ProfileTypeDefinition.objects.get(code='ALUMNO')
        status = SagaStatusDefinition.objects.get(profile_type=profile, code='INGRE')
        people = Identity.objects.bulk_create([
            Identity(category='FISICA', identifier=f'999{index:05}', first_name='Mismo', last_name='Apellido')
            for index in range(450)
        ])
        Affiliation.objects.bulk_create([Affiliation(identity=identity, profile_type=profile, current_status=status, reference='2') for identity in people])
        response = self.grid(page_size=25, page=10)
        page = response.context['page_obj']
        self.assertEqual([item['number'] for item in response.context['page_links']], list(page.paginator.get_elided_page_range(10, on_each_side=2, on_ends=1)))
        self.assertTrue(any(item['ellipsis'] for item in response.context['page_links']))
        ids = [identity.pk for identity in response.context['identities']]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(ids, [identity.pk for identity in self.grid(page_size=25, page=10).context['identities']])


@skipUnless(apps.is_installed('apps.platform_mocks'), 'Requiere platform_mocks.')
@override_settings(ENABLE_LOCAL_MOCKS=True, LUCY_DRY_RUN=False)
class TeamsConsoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.integrations.models import TeamWorkspace
        from apps.platform_mocks.models import MockAzureUser, MockMoodleCourse, MockMoodleEnrollment, MockMoodleUser, MockTeam, MockTeamMember
        cls.admin = get_user_model().objects.create_user('console-admin')
        cls.admin.groups.add(Group.objects.get(name='adm_alumnos_rol_admin'))
        cls.operator = get_user_model().objects.create_user('console-operator')
        cls.operator.groups.add(Group.objects.get(name='adm_alumnos_rol_operador'))
        cls.owner = MockAzureUser.objects.create(upn='owner@eco.unrc.edu.ar', first_name='Docente', last_name='Responsable')
        cls.mock_team = MockTeam.objects.create(display_name='Cátedra de prueba', owner=cls.owner)
        MockTeamMember.objects.create(team=cls.mock_team, user=cls.owner, is_owner=True)
        cls.team = TeamWorkspace.objects.create(pk=cls.mock_team.pk, display_name='Cátedra de prueba',
            owner_upn=cls.owner.upn, moodle_shortname='CATEDRA', career_code='2', commission='COMISION 1')
        course = MockMoodleCourse.objects.create(shortname='CATEDRA', fullname='Cátedra de prueba')
        cls.students = []
        for index in range(3):
            dni = str(77000000 + index)
            upn = f'a{dni}@eco.unrc.edu.ar'
            azure = MockAzureUser.objects.create(upn=upn, first_name='Alumno', last_name=f'Apellido{index}', document_number=dni)
            cls.students.append(azure)
            moodle = MockMoodleUser.objects.create(username=upn, email=upn, first_name='Alumno', last_name=f'Apellido{index}')
            MockMoodleEnrollment.objects.create(user=moodle, course=course, group_name='COMISION 1' if index < 2 else 'COMISION 2')
        MockTeamMember.objects.create(team=cls.mock_team, user=cls.students[0])

    def setUp(self):
        from apps.platform_mocks.clients import MockGraphClient, MockMoodleClient, MockSialClient
        self.client.force_login(self.admin)
        self.enterContext(patch('apps.portal.team_console.build_graph', return_value=MockGraphClient()))
        self.enterContext(patch('apps.portal.team_console.build_moodle', return_value=MockMoodleClient()))
        self.enterContext(patch('apps.portal.team_console.build_sial', return_value=MockSialClient()))
        self.http = self.enterContext(patch('requests.sessions.Session.request', side_effect=AssertionError('Red prohibida')))

    def tearDown(self):
        self.http.assert_not_called()

    def team_url(self, name):
        return reverse('portal:' + name, args=[self.team.pk])

    def test_teams_table_shows_counts_mapping_and_member_details(self):
        response = self.client.get(reverse('portal:teams'))
        team = response.context['teams'][0]
        self.assertEqual(team.member_count, 2)
        self.assertEqual(team.moodle_count, 2)
        self.assertEqual(team.parity, 'Discrepancia')
        self.assertContains(response, 'Poblar Equipo')
        self.assertContains(response, 'Purgar Cuatrimestre')
        self.assertContains(self.client.get(self.team_url('team_members')), 'owner@eco.unrc.edu.ar')

    def test_populate_from_moodle_is_idempotent_and_restores_parity_without_removing_owner(self):
        response = self.client.post(self.team_url('team_populate'), {'source': 'moodle'}, HTTP_HX_REQUEST='true')
        self.assertContains(response, '1 alumnos agregados')
        self.assertEqual(response.context['teams'][0].parity, 'Normal')
        self.assertEqual(self.mock_team.members.count(), 3)
        self.assertTrue(self.mock_team.members.filter(user=self.owner, is_owner=True).exists())
        self.assertFalse(self.mock_team.members.filter(user=self.students[2]).exists())
        response = self.client.post(self.team_url('team_populate'), {'source': 'moodle'}, HTTP_HX_REQUEST='true')
        self.assertContains(response, '0 alumnos agregados')

    def test_populate_from_sial_respects_career_and_commission(self):
        from apps.platform_mocks.models import MockSialRecord
        for index, student in enumerate(self.students):
            MockSialRecord.objects.create(dni=student.document_number, first_name=student.first_name, last_name=student.last_name,
                personal_email=f'alumno{index}@gmail.com', condition='INGRESANTE', career_code='2', modality='P',
                commission='COMISION 1' if index < 2 else 'COMISION 2')
        response = self.client.post(self.team_url('team_populate'), {'source': 'sial', 'campaign': '2026'}, HTTP_HX_REQUEST='true')
        self.assertContains(response, '1 alumnos agregados')
        self.assertEqual(self.mock_team.members.count(), 3)
        self.assertFalse(self.mock_team.members.filter(user=self.students[2]).exists())

    def test_mapping_and_refresh_update_local_snapshots(self):
        response = self.client.post(self.team_url('team_configure'), {
            'moodle_shortname': 'CATEDRA', 'career_code': '2', 'commission': 'COMISION 1'}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'Correspondencia pedagógica guardada')
        response = self.client.post(self.team_url('team_refresh'), {}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'Observaciones de Teams y Moodle actualizadas')
        self.team.refresh_from_db()
        self.assertEqual(len(self.team.members_snapshot), 2)
        self.assertEqual(len(self.team.moodle_snapshot), 2)
        self.assertIsNotNone(self.team.moodle_observed_at)

    def test_operator_cannot_populate_configure_refresh_or_read_team_members(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(self.team_url('team_members')).status_code, 403)
        for name in ('team_populate', 'team_configure', 'team_refresh'):
            self.assertEqual(self.client.post(self.team_url(name), {'source': 'moodle'}).status_code, 403)

    @override_settings(ENABLE_LOCAL_MOCKS=False)
    def test_missing_snapshots_are_unknown_not_false_zero_or_normal(self):
        response = self.client.get(reverse('portal:teams'))
        team = response.context['teams'][0]
        self.assertIsNone(team.member_count)
        self.assertIsNone(team.moodle_count)
        self.assertEqual(team.parity, 'Sin observación / mapeo')


@skipUnless(apps.is_installed('apps.platform_mocks'), 'Requiere platform_mocks.')
@override_settings(ENABLE_LOCAL_MOCKS=True, LUCY_DRY_RUN=False, LUCY_M365_SKU_ID='mirror-a1')
class CatalogMirrorsTests(TestCase):
    """Espejos relacionales, reemplazo completo, agenda y refresco puntual autorizado."""

    @classmethod
    def setUpTestData(cls):
        from apps.platform_mocks.models import MockAzureUser, MockTeam, MockTeamMember, MockMoodleUser, MockMoodleCourse, MockMoodleEnrollment
        cls.operator = get_user_model().objects.create_user('mirror-operador')
        cls.operator.groups.add(Group.objects.get(name='adm_alumnos_rol_operador'))
        cls.admin = get_user_model().objects.create_user('mirror-admin')
        cls.admin.groups.add(Group.objects.get(name='adm_alumnos_rol_admin'))
        cls.reader = get_user_model().objects.create_user('mirror-jefe')
        cls.reader.groups.add(Group.objects.get(name='adm_alumnos_rol_jefe'))
        cls.identity = Identity.objects.create(category='FISICA', identifier='88111111', first_name='Alumno', last_name='Espejo', institutional_upn='a88111111@eco.unrc.edu.ar')
        profile = ProfileTypeDefinition.objects.get(code='ALUMNO')
        Affiliation.objects.create(identity=cls.identity, profile_type=profile, current_status=SagaStatusDefinition.objects.get(profile_type=profile, code='INGRE'), reference='2')
        cls.azure = MockAzureUser.objects.create(upn=cls.identity.institutional_upn, first_name='Alumno', last_name='Espejo', assigned_sku='mirror-a1', password_profile_force_change=False, last_activity_at=timezone.now())
        cls.owner = MockAzureUser.objects.create(upn='docente@eco.unrc.edu.ar', first_name='Docente')
        cls.guest = MockAzureUser.objects.create(upn='invitado@example.test', first_name='Invitado')
        cls.team1 = MockTeam.objects.create(display_name='Equipo espejo primero', owner=cls.owner)
        cls.team2 = MockTeam.objects.create(display_name='Equipo espejo segundo', owner=cls.azure)
        MockTeamMember.objects.create(team=cls.team1, user=cls.azure)
        MockTeamMember.objects.create(team=cls.team1, user=cls.owner, is_owner=True)
        MockTeamMember.objects.create(team=cls.team1, user=cls.guest)
        MockTeamMember.objects.create(team=cls.team2, user=cls.azure, is_owner=True)
        cls.moodle_user = MockMoodleUser.objects.create(username=cls.azure.upn, email=cls.azure.upn, first_name='Alumno', last_name='Espejo')
        cls.course = MockMoodleCourse.objects.create(shortname='ESPEJO-I1', fullname='Curso espejo')
        MockMoodleEnrollment.objects.create(user=cls.moodle_user, course=cls.course)

    def setUp(self):
        from apps.platform_mocks.clients import MockGraphClient, MockMoodleClient
        self.graph, self.moodle = MockGraphClient(), MockMoodleClient()
        self.client.force_login(self.operator)
        self.enterContext(patch('requests.sessions.Session.request', side_effect=AssertionError('No consultar la red en pruebas con mocks.')))

    def sync(self, **kwargs):
        from apps.integrations.catalogs import sync_external_catalogs
        return sync_external_catalogs(graph=self.graph, moodle=self.moodle, **kwargs)

    def test_relational_membership_teams_counts_guests_and_uniqueness(self):
        from django.db import IntegrityError, transaction
        from django.db.models import Count
        from apps.integrations.models import TeamsMemberMirror, TeamsWorkspaceMirror
        self.assertEqual(self.sync(), {'TEAMS': 'SUCCEEDED', 'MOODLE': 'SUCCEEDED'})
        first = TeamsWorkspaceMirror.objects.get(team_id=str(self.team1.pk))
        self.assertTrue(TeamsMemberMirror.objects.filter(workspace=first, identity=self.identity).exists())
        self.assertEqual(set(self.identity.teams_memberships.values_list('workspace__team_id', flat=True)), {str(self.team1.pk), str(self.team2.pk)})
        self.assertEqual(TeamsWorkspaceMirror.objects.filter(members_count=1).count(), 1)
        self.assertEqual(TeamsWorkspaceMirror.objects.annotate(total=Count('members')).filter(total=1).count(), 1)
        self.assertIsNone(TeamsMemberMirror.objects.get(upn=self.guest.upn).identity_id)
        self.assertEqual((first.owners_count, first.members_count), (1, 3))
        with self.assertRaises(IntegrityError), transaction.atomic():
            TeamsMemberMirror.objects.create(workspace=first, upn=self.azure.upn, azure_user_id=str(self.azure.pk), role='member', synced_at=timezone.now())

    def test_sync_is_idempotent_prunes_removed_members_and_channels_and_records_state(self):
        from apps.platform_mocks.models import MockTeamMember
        from apps.integrations.models import TeamsMemberMirror, TeamsWorkspaceMirror, TeamsChannelMirror, MoodleCourseMirror, CatalogSyncState
        original = self.graph.get_team_channels
        with patch.object(self.graph, 'get_team_channels', side_effect=lambda team_id: original(team_id) + [
            {'id': team_id + ':privado', 'displayName': 'Comisión privada', 'membershipType': 'private'}]):
            self.sync()
        keys = list(TeamsMemberMirror.objects.order_by('pk').values_list('pk', flat=True))
        self.sync()
        self.assertEqual(list(TeamsMemberMirror.objects.order_by('pk').values_list('pk', flat=True)), keys)
        self.assertEqual(TeamsChannelMirror.objects.count(), 2)
        self.assertEqual(MoodleCourseMirror.objects.get(course_id=self.course.pk).enrolled_count, 1)
        MockTeamMember.objects.filter(team=self.team1, user__in=[self.owner, self.guest]).delete()
        self.sync()
        first = TeamsWorkspaceMirror.objects.get(team_id=str(self.team1.pk))
        self.assertEqual((first.members_count, first.owners_count), (1, 0))
        self.assertEqual(TeamsMemberMirror.objects.count(), 2)
        for state in CatalogSyncState.objects.all():
            self.assertEqual(state.last_status, 'SUCCEEDED')
            self.assertIsNotNone(state.last_run_at)
            self.assertGreater(state.next_run_at, state.last_run_at)
            self.assertIsNone(state.run_token)
        self.assertEqual(CatalogSyncState.objects.get(source='TEAMS').items_synced, 2)

    def test_sync_links_identity_by_azure_identifier_when_upn_changed(self):
        from apps.people.models import DigitalAccount
        from apps.integrations.models import TeamsMemberMirror
        DigitalAccount.objects.create(identity=self.identity, platform='TEAMS', account_identifier='cuenta-anterior', observations={'azure_user_id': str(self.azure.pk)})
        Identity.objects.filter(pk=self.identity.pk).update(institutional_upn='nuevo@eco.unrc.edu.ar')
        self.sync()
        self.assertEqual(TeamsMemberMirror.objects.filter(identity=self.identity).count(), 2)

    def test_failed_origin_preserves_previous_snapshot_and_other_origin_succeeds(self):
        from apps.integrations.models import TeamsMemberMirror, CatalogSyncState
        from apps.integrations.exceptions import IntegrationError
        self.sync()
        members = list(TeamsMemberMirror.objects.values_list('pk', 'upn'))
        with patch.object(self.graph, 'get_team_members', side_effect=IntegrationError('Fallo remoto de prueba')):
            self.assertEqual(self.sync(), {'TEAMS': 'FAILED', 'MOODLE': 'SUCCEEDED'})
        self.assertEqual(list(TeamsMemberMirror.objects.values_list('pk', 'upn')), members)
        state = CatalogSyncState.objects.get(source='TEAMS')
        self.assertEqual(state.last_status, 'FAILED')
        self.assertIn('IntegrationError', state.last_error)
        self.assertIsNone(state.run_token)

    def test_concurrent_lease_and_expired_lease_recovery(self):
        import uuid
        from datetime import timedelta
        from apps.integrations.models import CatalogSyncState
        CatalogSyncState.objects.filter(source='TEAMS').update(run_token=uuid.uuid4(), lock_expires_at=timezone.now() + timedelta(minutes=10))
        with patch.object(self.graph, 'list_teams') as remote:
            self.assertEqual(self.sync(sources=['TEAMS']), {'TEAMS': 'OMITIDO'})
            remote.assert_not_called()
        CatalogSyncState.objects.filter(source='TEAMS').update(lock_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.sync(sources=['TEAMS']), {'TEAMS': 'SUCCEEDED'})

    def test_late_worker_cannot_overwrite_another_lease(self):
        import uuid
        from apps.integrations.models import CatalogSyncState, TeamsWorkspaceMirror
        original = self.graph.list_teams
        next_token = uuid.uuid4()
        def replace_lease():
            CatalogSyncState.objects.filter(source='TEAMS').update(run_token=next_token)
            return original()
        with patch.object(self.graph, 'list_teams', side_effect=replace_lease):
            self.assertEqual(self.sync(sources=['TEAMS']), {'TEAMS': 'OMITIDO'})
        self.assertFalse(TeamsWorkspaceMirror.objects.exists())
        self.assertEqual(CatalogSyncState.objects.get(source='TEAMS').run_token, next_token)

    def test_scheduler_respects_saturday_local_time_and_only_due_origins(self):
        from datetime import datetime, timedelta
        from apps.integrations.catalogs import next_scheduled_run, sync_due_catalogs
        from apps.integrations.models import CatalogSyncState
        state = CatalogSyncState.objects.get(source='TEAMS')
        before = timezone.make_aware(datetime(2026, 11, 14, 0, 30))
        expected = timezone.make_aware(datetime(2026, 11, 14, 1, 0))
        self.assertEqual(next_scheduled_run(state, now=before), expected)
        self.assertEqual(next_scheduled_run(state, now=expected), expected + timedelta(days=7))
        CatalogSyncState.objects.update(next_run_at=timezone.now() + timedelta(days=1))
        CatalogSyncState.objects.filter(source='TEAMS').update(next_run_at=timezone.now() - timedelta(seconds=1))
        with patch('apps.integrations.catalogs.sync_external_catalogs', return_value={'TEAMS': 'SUCCEEDED'}) as task:
            self.assertEqual(sync_due_catalogs(), {'TEAMS': 'SUCCEEDED'})
            task.assert_called_once_with(sources=['TEAMS'])

    def test_sync_control_requires_admin_enqueues_task_and_edits_schedule(self):
        from apps.integrations.models import CatalogSyncState
        url = reverse('portal:catalog_sync')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'action': 'sync'}).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url), 'Sincronizar Catálogos Ahora')
        with patch('apps.integrations.tasks.sync_catalogs_task.delay') as enqueue:
            self.assertContains(self.client.post(url, {'action': 'sync'}), 'encolada')
            enqueue.assert_called_once_with()
        self.assertContains(self.client.post(url, {'source': 'TEAMS', 'schedule_weekday': '0', 'schedule_hour': '08:30'}), 'Programación semanal actualizada')
        state = CatalogSyncState.objects.get(source='TEAMS')
        self.assertEqual((state.schedule_weekday, state.schedule_hour.hour, state.schedule_hour.minute), (0, 8, 30))
        self.assertEqual(timezone.localtime(state.next_run_at).weekday(), 0)
        self.assertContains(self.client.post(url, {'source': 'TEAMS', 'schedule_weekday': '7', 'schedule_hour': '08:30'}), 'no es válida')

    def test_individual_refresh_updates_accounts_memberships_and_htmx_badges(self):
        from apps.platform_mocks.models import MockTeamMember, MockMoodleEnrollment
        from apps.integrations.models import TeamsMemberMirror, TeamsWorkspaceMirror
        from apps.people.models import DigitalAccount
        self.sync()
        MockTeamMember.objects.filter(team=self.team1, user=self.azure).delete()
        MockMoodleEnrollment.objects.filter(user=self.moodle_user).delete()
        url = reverse('portal:identity_platform_refresh', args=[self.identity.pk])
        with patch('apps.integrations.catalogs.catalog_clients', return_value=(self.graph, self.moodle)):
            response = self.client.post(url, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'Estado de Teams y Moodle actualizado')
        self.assertNotContains(response, '<html')
        self.assertContains(response, self.team2.display_name)
        self.assertNotContains(response, self.team1.display_name)
        self.assertEqual(TeamsMemberMirror.objects.filter(identity=self.identity).count(), 1)
        self.assertEqual(TeamsWorkspaceMirror.objects.get(team_id=str(self.team1.pk)).members_count, 2)
        self.assertEqual(DigitalAccount.objects.get(identity=self.identity, platform='MOODLE').observations['courses'], [])
        self.assertTrue(DigitalAccount.objects.get(identity=self.identity, platform='TEAMS').observations['license_assigned'])
        self.assertContains(self.client.get(reverse('portal:identity360_plural', args=[self.identity.pk])), 'Actualizar estado (Teams / Moodle)')

    def test_individual_refresh_is_post_only_requires_permission_csrf_and_preserves_on_failure(self):
        from apps.integrations.exceptions import IntegrationError
        from apps.people.models import DigitalAccount
        url = reverse('portal:identity_platform_refresh', args=[self.identity.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.operator)
        self.assertEqual(csrf.post(url).status_code, 403)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.operator)
        with patch.object(self.moodle, 'get_user_courses', side_effect=IntegrationError('Fallo de lectura')), patch('apps.integrations.catalogs.catalog_clients', return_value=(self.graph, self.moodle)):
            self.assertContains(self.client.post(url), 'no completó la operación')
        self.assertFalse(DigitalAccount.objects.filter(identity=self.identity).exists())

    def test_selectors_use_relational_catalog_without_remote_get(self):
        self.sync()
        self.client.force_login(self.admin)
        response = self.client.get(reverse('portal:catalog_channels'), {'team_id': str(self.team1.pk)})
        self.assertContains(response, 'General')
        self.assertContains(self.client.get(reverse('portal:teams')), self.team1.display_name)
        self.assertContains(self.client.get(reverse('portal:identity360', args=[self.identity.pk])), self.course.shortname)

    def test_full_catalog_prunes_removed_workspaces_and_courses(self):
        from apps.platform_mocks.models import MockTeam, MockMoodleCourse
        from apps.integrations.models import TeamsWorkspaceMirror, MoodleCourseMirror, TeamsMemberMirror
        self.sync()
        MockTeam.objects.filter(pk=self.team1.pk).delete()
        MockMoodleCourse.objects.filter(pk=self.course.pk).delete()
        self.assertEqual(self.sync(), {'TEAMS': 'SUCCEEDED', 'MOODLE': 'SUCCEEDED'})
        self.assertFalse(TeamsWorkspaceMirror.objects.filter(team_id=str(self.team1.pk)).exists())
        self.assertFalse(MoodleCourseMirror.objects.exists())
        self.assertEqual(TeamsMemberMirror.objects.count(), 1)

    def test_dry_run_catalog_never_purges_confirmed_mirrors(self):
        from apps.integrations.models import TeamsWorkspaceMirror, CatalogSyncState
        self.sync()
        self.graph.dry_run = True
        self.assertEqual(self.sync(sources=['TEAMS']), {'TEAMS': 'FAILED'})
        self.assertEqual(TeamsWorkspaceMirror.objects.count(), 2)
        self.assertEqual(CatalogSyncState.objects.get(source='TEAMS').last_status, 'FAILED')


class CatalogClientContractTests(TestCase):
    """Contratos de lectura completa y continuaciones Graph restringidas al mismo recurso."""

    def test_graph_catalog_and_joined_teams_read_all_pages(self):
        from apps.integrations.services.graph import MicrosoftGraphClient
        graph = MicrosoftGraphClient()
        for method, path in ((graph.list_teams, 'groups'), (lambda: graph.get_team_channels('equipo'), 'teams/equipo/channels'),
                             (lambda: graph.get_user_joined_teams('usuario'), 'users/usuario/joinedTeams')):
            replies = [Mock(json=lambda: {'value': [{'id': 'primero'}], '@odata.nextLink': f'{graph.graph_base_url}/{path}?$skiptoken=segunda'}),
                       Mock(json=lambda: {'value': [{'id': 'segundo'}]})]
            with patch.object(graph, '_graph_request', side_effect=replies) as request:
                result = method()
            self.assertEqual(len(result), 2)
            self.assertEqual(request.call_count, 2)
            self.assertEqual(request.call_args.args[1], path + '?$skiptoken=segunda')

    def test_graph_catalog_rejects_foreign_or_circular_continuations_and_malformed_pages(self):
        from apps.integrations.services.graph import MicrosoftGraphClient
        from apps.integrations.exceptions import IntegrationError
        graph = MicrosoftGraphClient()
        for body in ({'value': [], '@odata.nextLink': 'https://ajeno.example/groups?token=1'},
                     {'value': None}, {'value': [{}]}):
            with patch.object(graph, '_graph_request', return_value=Mock(json=lambda: body)), self.assertRaises(IntegrationError):
                graph.list_teams()
        body = {'value': [], '@odata.nextLink': graph.graph_base_url + '/groups?pagina=1'}
        with patch.object(graph, '_graph_request', return_value=Mock(json=lambda: body)), self.assertRaises(IntegrationError):
            graph.list_teams()

    def test_moodle_catalog_resolves_category_and_user_courses_without_access_logs(self):
        from apps.integrations.services.moodle import MoodleRestClient
        moodle = MoodleRestClient()
        with patch.object(moodle, '_call', side_effect=[[{'id': 3, 'name': 'Grado'}],
                [{'id': 1, 'shortname': 'I1', 'fullname': 'Ingreso', 'categoryid': 3}]]) as remote:
            self.assertEqual(moodle.list_courses()[0]['category_name'], 'Grado')
        self.assertEqual([call.args[0] for call in remote.call_args_list], ['core_course_get_categories', 'core_course_get_courses'])
        with patch.object(moodle, '_call', return_value=[{'id': 1, 'shortname': 'I1'}]) as remote:
            self.assertEqual(moodle.get_user_courses(12)[0]['shortname'], 'I1')
            remote.assert_called_once_with('core_enrol_get_users_courses', data={'userid': 12, 'returnusercount': 0})


@skipUnless(apps.is_installed('apps.platform_mocks'), 'Requiere platform_mocks.')
@override_settings(ENABLE_LOCAL_MOCKS=True, LUCY_DRY_RUN=False, LUCY_M365_SKU_ID='release-a1')
class ReleaseLayoutSeedTests(TestCase):
    """Semilla reproducible del release y estructura adaptable de la navegación institucional."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user('release-admin')
        cls.admin.groups.add(Group.objects.get(name='adm_alumnos_rol_admin'))

    def setUp(self):
        self.client.force_login(self.admin)

    def test_seed_mirrors_are_idempotent_and_survive_catalog_sync(self):
        from io import StringIO
        from django.core.management import call_command
        from django.db.models import Count
        from apps.integrations.catalogs import sync_external_catalogs
        from apps.integrations.models import TeamsWorkspaceMirror, TeamsChannelMirror, TeamsMemberMirror, MoodleCourseMirror
        from apps.platform_mocks.models import MockTeam, MockTeamMember, MockTeamChannel, MockMoodleEnrollment, MockMoodleUser, MockMoodleCourse
        from apps.platform_mocks.clients import MockGraphClient, MockMoodleClient
        output = StringIO()
        legacy = MockMoodleCourse.objects.create(shortname='DEV-ALUMNOS', fullname='Curso de desarrollo anterior')
        old_user = MockMoodleUser.objects.create(username='a95000000@eco.unrc.edu.ar', email='a95000000@eco.unrc.edu.ar', first_name='Alumno anterior')
        MockMoodleEnrollment.objects.create(user=old_user, course=legacy)
        MoodleCourseMirror.objects.create(course_id=legacy.pk, shortname='DEV-ALUMNOS', fullname=legacy.fullname, synced_at=timezone.now())
        call_command('seed_dev_students', count=60, stdout=output)
        self.assertFalse(MockMoodleCourse.objects.filter(shortname='DEV-ALUMNOS').exists())
        original = {
            'teams': list(TeamsWorkspaceMirror.objects.order_by('pk').values_list('pk', flat=True)),
            'channels': list(TeamsChannelMirror.objects.order_by('pk').values_list('pk', flat=True)),
            'members': list(TeamsMemberMirror.objects.order_by('pk').values_list('pk', flat=True)),
            'courses': list(MoodleCourseMirror.objects.order_by('pk').values_list('pk', flat=True)),
        }
        call_command('seed_dev_students', count=60, stdout=output)
        self.assertEqual(original['teams'], list(TeamsWorkspaceMirror.objects.order_by('pk').values_list('pk', flat=True)))
        self.assertEqual(original['channels'], list(TeamsChannelMirror.objects.order_by('pk').values_list('pk', flat=True)))
        self.assertEqual(original['members'], list(TeamsMemberMirror.objects.order_by('pk').values_list('pk', flat=True)))
        self.assertEqual(original['courses'], list(MoodleCourseMirror.objects.order_by('pk').values_list('pk', flat=True)))
        self.assertEqual((TeamsWorkspaceMirror.objects.count(), TeamsChannelMirror.objects.count(), TeamsMemberMirror.objects.count(), MoodleCourseMirror.objects.count()), (3, 6, 61, 3))
        self.assertEqual(set(MoodleCourseMirror.objects.values_list('course_id', 'fullname')), {
            (9040, 'Introducción a la Economía'), (9041, 'Contabilidad I'), (9042, 'Administración General')})
        self.assertEqual(TeamsMemberMirror.objects.filter(identity__metadata__seed='seed_dev_students').values('identity_id').distinct().count(), 60)
        self.assertEqual(TeamsMemberMirror.objects.filter(identity=None).count(), 1)
        self.assertEqual(TeamsWorkspaceMirror.objects.filter(members_count=1, owners_count=0).count(), 1)
        self.assertEqual(sorted(TeamsWorkspaceMirror.objects.values_list('members_count', flat=True)), [1, 29, 31])
        for team in TeamsWorkspaceMirror.objects.annotate(actual_count=Count('members')):
            self.assertEqual(team.members_count, team.actual_count)
            self.assertEqual(team.owners_count, team.members.filter(role='owner').count())
            self.assertEqual(set(team.channels.values_list('display_name', 'membership_type')), {('General', 'standard'), ('Comisión 1', 'private')})
        self.assertEqual((MockTeam.objects.count(), MockTeamChannel.objects.count(), MockTeamMember.objects.count()), (3, 6, 61))
        self.assertEqual(MockMoodleEnrollment.objects.filter(user__username__startswith='a95').count(), 60)
        with patch('requests.sessions.Session.request', side_effect=AssertionError('La semilla y los mocks no consultan la red.')):
            self.assertEqual(sync_external_catalogs(graph=MockGraphClient(), moodle=MockMoodleClient()), {'TEAMS': 'SUCCEEDED', 'MOODLE': 'SUCCEEDED'})
        self.assertEqual((TeamsWorkspaceMirror.objects.count(), TeamsChannelMirror.objects.count(), TeamsMemberMirror.objects.count(), MoodleCourseMirror.objects.count()), (3, 6, 61, 3))
        self.assertIn('3 equipos, 6 canales, 61 miembros y 3 cursos', output.getvalue())

    def test_responsive_sidebar_full_width_active_navigation_and_horizontal_grid(self):
        from html.parser import HTMLParser
        class LayoutParser(HTMLParser):
            """Releva atributos de navegación sin depender del formato del HTML."""
            def __init__(self):
                super().__init__()
                self.tags = []
            def handle_starttag(self, tag, attrs):
                self.tags.append((tag, dict(attrs)))
        response = self.client.get(reverse('portal:administracion'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'max-w-7xl')
        self.assertContains(response, 'Menú Administración')
        self.assertContains(response, 'overflow-x-auto')
        self.assertContains(response, 'prefers-reduced-motion')
        parser = LayoutParser()
        parser.feed(response.content.decode())
        aside = next(attrs for tag, attrs in parser.tags if tag == 'aside')
        self.assertEqual(aside['id'], 'administration-sidebar')
        self.assertNotIn('rounded-2xl', aside['class'])
        button = next(attrs for tag, attrs in parser.tags if attrs.get('id') == 'admin-menu-toggle')
        self.assertEqual((button['aria-controls'], button['aria-expanded']), ('admin-navigation', 'false'))
        self.assertIn('lg:hidden', button['class'])
        active = [attrs['href'] for tag, attrs in parser.tags if tag == 'a' and attrs.get('aria-current') == 'page']
        self.assertEqual(active, [reverse('portal:administracion')])
        builder = self.client.get(reverse('portal:query_builder'))
        parser = LayoutParser()
        parser.feed(builder.content.decode())
        active = [attrs['href'] for tag, attrs in parser.tags if tag == 'a' and attrs.get('aria-current') == 'page']
        self.assertEqual(active, [reverse('portal:query_builder')])
        self.admin.groups.add(Group.objects.get(name='adm_posgrado_rol_jefe'))
        self.assertNotContains(self.client.get(reverse('portal:postgrado')), 'max-w-7xl')
