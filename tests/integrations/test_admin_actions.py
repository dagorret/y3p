"""Contrato de acciones Graph/Teams y Moodle sin redes externas."""

from dataclasses import FrozenInstanceError
from unittest import TestCase
from unittest.mock import Mock, call

from apps.integrations.dataclasses import ExternalActionResult, GraphUserResult
from apps.integrations.exceptions import IntegrationError
from apps.integrations.services.graph import MicrosoftGraphClient
from apps.integrations.services.moodle import MoodleRestClient


class AdminIntegrationTests(TestCase):
    def test_add_team_member_dry_run_and_real_payload(self):
        session = Mock()
        dry = MicrosoftGraphClient(dry_run=True, session=session)
        self.assertTrue(dry.add_team_member('team', 'ada@eco.unrc.edu.ar').simulated)
        self.assertFalse(session.mock_calls)
        graph = MicrosoftGraphClient()
        graph._write = Mock()
        self.assertTrue(graph.add_team_member('team', 'ada@eco.unrc.edu.ar').success)
        payload = graph._write.call_args.kwargs['json']
        self.assertEqual(payload['roles'], [])
        self.assertIn('ada%40eco.unrc.edu.ar', payload['user@odata.bind'])

    def test_team_members_resolve_upn_from_user_id(self):
        graph = MicrosoftGraphClient()
        graph._graph_request = Mock(return_value=Mock(json=lambda: {'value': [
            {'id': 'member', 'userId': 'id-ada', 'email': 'alias@example.test', 'displayName': 'Ada', 'roles': ['owner']},
        ]}))
        graph.get_user = Mock(return_value=GraphUserResult('id-ada', 'ada@eco.unrc.edu.ar', 'Ada', False))
        members = graph.get_team_members('team')
        self.assertEqual(members[0].upn, 'ada@eco.unrc.edu.ar')
        self.assertTrue(members[0].is_owner)

    def test_moodle_course_participants_have_roles_and_groups(self):
        moodle = MoodleRestClient()
        moodle._call = Mock(return_value=[{'id': 10, 'username': 'ada@eco.unrc.edu.ar', 'firstname': 'Ada',
            'lastname': 'Lovelace', 'roles': [{'roleid': 5}], 'groups': [{'name': 'COMISION 1'}]}])
        student = moodle.get_course_participants(55)[0]
        self.assertEqual(student.username, 'ada@eco.unrc.edu.ar')
        self.assertEqual(student.role_ids, (5,))
        self.assertEqual(student.groups, ('COMISION 1',))

    def test_organizational_graph_user_can_be_created_without_surname(self):
        graph = MicrosoftGraphClient()
        graph._write = Mock(return_value=Mock(json=lambda: {'id': 'functional-id'}))
        result = graph.create_user('Cátedra de Economía', '', 'catedra@eco.unrc.edu.ar', 'Secret!')
        self.assertTrue(result.created)
        self.assertNotIn('surname', graph._write.call_args.kwargs['json'])

    def test_all_new_writes_bypass_network_in_dry_run(self):
        session = Mock()
        graph = MicrosoftGraphClient(dry_run=True, session=session)
        moodle = MoodleRestClient(dry_run=True, session=session)
        results = [graph.revoke_sign_in_sessions('ada'), graph.reset_password_temporary('ada', 'Secret!'),
                   graph.toggle_user_status('ada', False), graph.toggle_license('ada', 'sku', True),
                   graph.toggle_license('ada', 'sku', False), graph.create_team_from_template('Curso', 'Descripción', 'ada'),
                   graph.purge_team_members('team'), moodle.unenrol_user(1, 2), moodle.change_user_group(1, 2, 3, 4)]
        self.assertTrue(all(result.success and result.simulated for result in results))
        self.assertEqual(graph.get_user_license_ids('ada'), ())
        self.assertFalse(session.mock_calls)
        with self.assertRaises(FrozenInstanceError):
            results[0].success = False

    def test_graph_revoke_reset_status_and_license_payloads(self):
        graph = MicrosoftGraphClient()
        graph._write = Mock(return_value=Mock(json=lambda: {'value': True}))
        self.assertTrue(graph.revoke_sign_in_sessions('ada@example.test').success)
        graph.reset_password_temporary('ada@example.test', 'Secret!')
        self.assertEqual(graph._write.call_args.kwargs['json'], {'passwordProfile': {'password': 'Secret!', 'forceChangePasswordNextSignIn': True}})
        graph.toggle_user_status('ada@example.test', False)
        self.assertEqual(graph._write.call_args.kwargs['json'], {'accountEnabled': False})
        graph.toggle_license('ada@example.test', 'sku', True)
        self.assertEqual(graph._write.call_args.kwargs['json'], {'addLicenses': [{'skuId': 'sku'}], 'removeLicenses': []})
        graph.toggle_license('ada@example.test', 'sku', False)
        self.assertEqual(graph._write.call_args.kwargs['json'], {'addLicenses': [], 'removeLicenses': ['sku']})

    def test_graph_revoke_does_not_claim_success_for_false_response(self):
        graph = MicrosoftGraphClient()
        graph._write = Mock(return_value=Mock(json=lambda: {'value': False}))
        with self.assertRaises(IntegrationError):
            graph.revoke_sign_in_sessions('ada')

    def test_license_read_requires_explicit_remote_state(self):
        graph = MicrosoftGraphClient()
        graph._graph_request = Mock(return_value=Mock(json=lambda: {'assignedLicenses': [{'skuId': 'sku'}]}))
        self.assertEqual(graph.get_user_license_ids('ada'), ('sku',))
        graph._graph_request.return_value.json = lambda: {}
        with self.assertRaises(IntegrationError):
            graph.get_user_license_ids('ada')

    def test_team_education_template_owner_and_async_acceptance(self):
        graph = MicrosoftGraphClient()
        graph.get_user = Mock(return_value=GraphUserResult('owner-id', 'ada', 'Ada', False))
        graph._write = Mock(return_value=Mock(status_code=202, headers={'Location': '/teams/team/operations/1'}))
        result = graph.create_team_from_template('Curso', 'Descripción', 'ada')
        payload = graph._write.call_args.kwargs['json']
        self.assertIn('educationClass', payload['template@odata.bind'])
        self.assertEqual(payload['members'][0]['roles'], ['owner'])
        self.assertTrue(result.accepted)

    def test_purge_paginates_before_deleting_and_preserves_owners(self):
        graph = MicrosoftGraphClient()
        graph._graph_request = Mock(side_effect=[
            Mock(json=lambda: {'value': [{'id': 'owner', 'roles': ['owner']}, {'id': 'm/1', 'roles': []}],
                               '@odata.nextLink': 'https://graph.microsoft.com/v1.0/teams/team/members?$skiptoken=next'}),
            Mock(json=lambda: {'value': [{'id': 'm2', 'roles': []}]}),
        ])
        graph._write = Mock()
        self.assertEqual(graph.purge_team_members('team').affected_count, 2)
        self.assertEqual(graph._write.call_args_list, [call('DELETE', 'teams/team/members/m%2F1'), call('DELETE', 'teams/team/members/m2')])

    def test_purge_rejects_external_pagination_before_any_deletion(self):
        graph = MicrosoftGraphClient()
        graph._graph_request = Mock(return_value=Mock(json=lambda: {'value': [], '@odata.nextLink': 'https://external.test/steal'}))
        graph._write = Mock()
        with self.assertRaises(IntegrationError):
            graph.purge_team_members('team')
        graph._write.assert_not_called()

    def test_moodle_unenrol_and_group_change_use_expected_functions(self):
        moodle = MoodleRestClient()
        moodle._call = Mock(return_value=None)
        self.assertIsInstance(moodle.unenrol_user(1, 2), ExternalActionResult)
        self.assertEqual(moodle._call.call_args.args[0], 'enrol_manual_unenrol_users')
        moodle._call.reset_mock()
        result = moodle.change_user_group(1, 2, 3, 4)
        self.assertTrue(result.success)
        self.assertEqual([c.args[0] for c in moodle._call.call_args_list], ['core_group_delete_group_members', 'core_group_add_group_members'])
