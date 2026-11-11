"""Matriz de convertibilidad, prioridades y ejecución Moodle de ingreso."""

from importlib import import_module
from unittest.mock import Mock, call

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from apps.integrations.dataclasses import MoodleEnrollmentResult, MoodleGroupResult, MoodleUserResult
from apps.integrations.exceptions import RemoteServiceUnavailableError
from apps.integrations.models import AdmissionCourseRule
from apps.people.models import Affiliation, Identity, OutboxEvent, ProfileTypeDefinition, SagaStatusDefinition
from apps.provisioning.orchestrator import run_moodle_step


class AdmissionCourseRuleTests(TestCase):
    def setUp(self):
        AdmissionCourseRule.objects.all().delete()
        import_module('apps.integrations.migrations.0002_seed_admission_course_rules').seed_rules(
            apps, type('SchemaEditor', (), {'connection': connection})()
        )

    def test_seed_is_idempotent_and_exact_commissions_select_i1_or_i2(self):
        self.assertEqual(AdmissionCourseRule.objects.count(), 11)
        import_module('apps.integrations.migrations.0002_seed_admission_course_rules').seed_rules(
            apps, type('SchemaEditor', (), {'connection': connection})()
        )
        self.assertEqual(AdmissionCourseRule.objects.count(), 11)
        for career in ('2', '3', '4'):
            for commission in ('1', '2'):
                with self.subTest(career=career, commission=commission):
                    self.assertEqual(AdmissionCourseRule.resolve_courses(career, 'P', f'COMISION {commission}'),
                                     (['I3', 'I4', 'I5', 'I6', f'I{commission}'], f'COMISION {commission}'))

    def test_fallback_and_other_careers(self):
        for commission in ('', 'COMISION 10', 'OTRA'):
            self.assertEqual(AdmissionCourseRule.resolve_courses('2', 'D', commission),
                             (['I3', 'I4', 'I5', 'I6'], None))
        self.assertEqual(AdmissionCourseRule.resolve_courses('7', '', ''), (['I7'], None))
        self.assertEqual(AdmissionCourseRule.resolve_courses('8', '', ''), (['I8'], None))
        self.assertEqual(AdmissionCourseRule.resolve_courses('999', '', ''), ([], None))

    def test_first_match_wins_and_inactive_rules_are_ignored(self):
        shortcut = AdmissionCourseRule.objects.create(order=0, target_courses=' X, Y, , X ', target_group=' Grupo ')
        self.assertEqual(AdmissionCourseRule.resolve_courses('2', 'P', 'COMISION 1'), (['X', 'Y'], 'Grupo'))
        shortcut.is_active = False
        shortcut.save()
        self.assertEqual(AdmissionCourseRule.resolve_courses('2', 'P', 'COMISION 1')[0][-1], 'I1')

    def test_same_priority_uses_id_and_modality_is_a_filter(self):
        AdmissionCourseRule.objects.create(order=0, career_code='2', modality='D', target_courses='DIST')
        first = AdmissionCourseRule.objects.create(order=0, career_code='', modality='P', target_courses='FIRST')
        AdmissionCourseRule.objects.create(order=0, modality='P', target_courses='SECOND')
        self.assertEqual(AdmissionCourseRule.resolve_courses(' 2 ', ' P ', ''), (['FIRST'], None))
        self.assertEqual(AdmissionCourseRule.resolve_courses('2', 'D', ''), (['DIST'], None))
        self.assertTrue(first.is_active)

    def test_regex_and_invalid_configuration_validation(self):
        rule = AdmissionCourseRule.objects.create(order=0, commission_pattern=r'COMISION [12]', target_courses='REGEX')
        self.assertEqual(AdmissionCourseRule.resolve_courses('2', '1', ' comision 2 '), (['REGEX'], None))
        rule.commission_pattern = '['
        with self.assertRaises(ValidationError):
            rule.full_clean()
        rule.commission_pattern = None
        rule.target_courses = ' , '
        with self.assertRaises(ValidationError):
            rule.full_clean()

    def make_event(self, *, commission='COMISION 1', profile_code='ALUMNO', status_code='INGRE'):
        identity = Identity.objects.create(category='FISICA', identifier='12345678', first_name='Ana', last_name='Pérez')
        profile, _ = ProfileTypeDefinition.objects.get_or_create(code=profile_code, defaults={'name': profile_code})
        status, _ = SagaStatusDefinition.objects.get_or_create(
            profile_type=profile, code=status_code, defaults={'name': status_code}
        )
        affiliation = Affiliation.objects.create(identity=identity, profile_type=profile, current_status=status, reference='2')
        return OutboxEvent.objects.create(
            identity=identity, affiliation=affiliation, action='PROVISION', status=OutboxEvent.Status.WAITING_SUBTASK,
            payload={'upn': 'ana@example.test', 'first_name': 'Ana', 'last_name': 'Pérez',
                     'career_code': '2', 'modality': 'P', 'commission': commission,
                     'profile_type_code': profile_code, 'provisioning_status': status_code, 'course_shortname': 'POS-2026'},
        )

    def moodle_mock(self):
        moodle = Mock()
        moodle.create_user.return_value = MoodleUserResult(88, 'ana', 'ana@example.test', True)
        moodle.get_course_by_shortname.side_effect = lambda name: {'id': int(name[1:]), 'shortname': name}
        moodle.enrol_user.side_effect = lambda user, course, role_id: MoodleEnrollmentResult(user, course, role_id, True)
        moodle.get_or_create_group.side_effect = lambda course, name: course + 100
        moodle.add_user_to_group.side_effect = lambda group, user, course_id: MoodleGroupResult(group, course_id, user, 'add', True)
        return moodle

    def test_orchestrator_enrols_all_courses_and_assigns_group_per_course(self):
        event, moodle = self.make_event(), self.moodle_mock()
        self.assertEqual(run_moodle_step(event.pk, moodle=moodle), OutboxEvent.Status.PENDING)
        moodle.enrol_user.assert_has_calls([call(88, course, role_id=5) for course in (3, 4, 5, 6, 1)])
        self.assertEqual(moodle.enrol_user.call_count, 5)
        moodle.add_user_to_group.assert_has_calls([call(course + 100, 88, course_id=course) for course in (3, 4, 5, 6, 1)])
        self.assertEqual(moodle.add_user_to_group.call_count, 5)
        event.refresh_from_db()
        self.assertEqual([c['shortname'] for c in event.step_results['moodle']['details']['courses']], ['I3', 'I4', 'I5', 'I6', 'I1'])
        run_moodle_step(event.pk, moodle=moodle)
        self.assertEqual(moodle.enrol_user.call_count, 5)

    def test_orchestrator_uses_affiliation_for_legacy_payload_and_no_commission_fallback(self):
        event, moodle = self.make_event(commission=''), self.moodle_mock()
        for key in ('career_code', 'profile_type_code', 'provisioning_status'):
            event.payload.pop(key)
        event.save(update_fields=['payload'])
        self.assertEqual(run_moodle_step(event.pk, moodle=moodle), OutboxEvent.Status.PENDING)
        self.assertEqual(moodle.enrol_user.call_count, 4)
        moodle.get_or_create_group.assert_not_called()

    def test_other_profiles_keep_single_course_resolution(self):
        event, moodle = self.make_event(profile_code='POSGRADO', status_code='REGULAR'), self.moodle_mock()
        moodle.get_course_by_shortname.side_effect = None
        moodle.get_course_by_shortname.return_value = {'id': 2026}
        self.assertEqual(run_moodle_step(event.pk, moodle=moodle), OutboxEvent.Status.PENDING)
        moodle.get_course_by_shortname.assert_called_once_with('POS-2026')
        moodle.enrol_user.assert_called_once_with(88, 2026, role_id=5)

    def test_missing_course_fails_without_confirming_checkpoint(self):
        event, moodle = self.make_event(), self.moodle_mock()
        moodle.get_course_by_shortname.side_effect = lambda name: None if name == 'I4' else {'id': 3}
        self.assertEqual(run_moodle_step(event.pk, moodle=moodle), OutboxEvent.Status.FAILED)
        event.refresh_from_db()
        self.assertEqual(event.step_results['moodle']['status'], 'FAILED')

    def test_transient_error_is_retried_without_false_confirmation(self):
        event, moodle = self.make_event(), self.moodle_mock()
        moodle.enrol_user.side_effect = RemoteServiceUnavailableError('offline')
        self.assertEqual(run_moodle_step(event.pk, moodle=moodle), OutboxEvent.Status.RETRY_SCHEDULED)
        event.refresh_from_db()
        self.assertEqual(event.step_results['moodle']['status'], 'FAILED')

    def test_admin_renders_and_is_reserved_for_superusers(self):
        user = get_user_model().objects.create_superuser('rules-admin', password='test')
        self.client.force_login(user)
        url = reverse('admin:integrations_admissioncourserule_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'COMISION 1')
        user.is_superuser = False
        user.save()
        self.assertEqual(self.client.get(url).status_code, 403)
