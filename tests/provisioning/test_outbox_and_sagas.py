"""Concurrency and saga regression tests using database rows and provider doubles."""

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from django.db import connection, transaction
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.integrations.dataclasses import (
    GraphUserResult,
    LicenseResult,
    MailDeliveryResult,
    MoodleEnrollmentResult,
    MoodleUserResult,
)
from apps.integrations.exceptions import (
    AuthenticationError,
    RateLimitExceededError,
    RemoteServiceUnavailableError,
)
from apps.people.models import (
    Affiliation,
    Identity,
    OutboxEvent,
    PersonalProfile,
    ProfileTypeDefinition,
    SagaStatusDefinition,
    SagaTransitionRule,
)
from apps.people.services import create_affiliation, transition_affiliation
from apps.provisioning.dispatcher import claim_events
from apps.provisioning.orchestrator import record_failure, run_moodle_step, run_saga
from apps.provisioning.sweeper import sweep_stuck_events
from apps.provisioning.tasks import moodle_step as moodle_celery_task


class OutboxAndSagaTests(TransactionTestCase):
    """Verify transactional emission, FIFO claims, continuation and checkpoints."""

    reset_sequences = True

    def setUp(self):
        """Ensure minimal seeded graph survives TransactionTestCase flush cycles."""

        self.profile, _ = ProfileTypeDefinition.objects.get_or_create(
            code="ALUMNO",
            defaults={"name": "Alumno", "allows_multiple_instances": True},
        )
        specs = [
            ("PRE", "Preinscripto", True, False, 60, "", False),
            ("ASP", "Aspirante", False, False, 180, "", False),
            ("INGRE", "Ingresante", False, False, 365, "PROVISION", True),
            ("ALUMNO", "Alumno regular", False, False, None, "PROVISION", True),
            ("GRADUADO", "Graduado", False, True, None, "SUSPEND", False),
        ]
        for code, name, initial, terminal, ttl, action, licensed in specs:
            SagaStatusDefinition.objects.update_or_create(
                profile_type=self.profile,
                code=code,
                defaults={
                    "name": name,
                    "is_initial": initial,
                    "is_terminal": terminal,
                    "default_ttl_days": ttl,
                    "provisioning_action": action,
                    "requires_m365_license": licensed,
                },
            )
        self.statuses = {
            code: SagaStatusDefinition.objects.get(profile_type=self.profile, code=code)
            for code, *_ in specs
        }
        for from_code, to_code, action in [
            ("PRE", "ASP", "Confirmar Bedelía"),
            ("ASP", "INGRE", "Matricular Ingreso"),
        ]:
            SagaTransitionRule.objects.update_or_create(
                profile_type=self.profile,
                from_status=self.statuses[from_code],
                to_status=self.statuses[to_code],
                defaults={"action_name": action},
            )

        teacher, _ = ProfileTypeDefinition.objects.get_or_create(
            code="PROFESOR", defaults={"name": "Profesor"}
        )
        SagaStatusDefinition.objects.update_or_create(
            profile_type=teacher,
            code="SI",
            defaults={
                "name": "Servicio activo",
                "is_initial": True,
                "is_terminal": False,
                "provisioning_action": "PROVISION",
                "requires_m365_license": True,
            },
        )
        self.identity = self._identity()

    def _identity(self):
        """Create a unique person and personal contact for outbox payload tests."""

        token = uuid4().hex[:10]
        identity = Identity.objects.create(
            category=Identity.Category.FISICA,
            identifier=f"dni-{token}",
            first_name="Ada",
            last_name="Lovelace",
            institutional_upn=f"a{token}@eco.unrc.edu.ar",
        )
        PersonalProfile.objects.create(
            identity=identity, personal_email=f"{token}@example.test"
        )
        return identity

    def _event(self, *, identity=None, action="PROVISION", course="9040", required=False,
               event_status=OutboxEvent.Status.PENDING):
        """Create a minimal durable event and its real affiliation references."""

        identity = identity or self.identity
        status = self.statuses["INGRE"]
        affiliation = Affiliation.objects.create(
            identity=identity,
            profile_type=self.profile,
            current_status=status,
            reference=course,
            is_active=True,
        )
        return OutboxEvent.objects.create(
            action=action,
            identity=identity,
            affiliation=affiliation,
            payload={
                "identifier": identity.identifier,
                "upn": identity.institutional_upn,
                "first_name": identity.first_name,
                "last_name": identity.last_name,
                "full_name": f"{identity.first_name} {identity.last_name}",
                "recipient": identity.personal_profile.personal_email,
                "course_shortname": course,
                "group_name": "",
                "requires_m365_license": required,
            },
            status=event_status,
        )

    def test_action_is_emitted_with_create_and_transition_and_rollback_is_atomic(self):
        """Outbox follows status action and disappears with a rolled-back producer unit."""

        pre = SagaStatusDefinition.objects.get(profile_type=self.profile, code="PRE")
        pre.provisioning_action = "PROVISION"
        pre.save(update_fields=["provisioning_action"])
        with mock.patch("apps.people.services.current_app.send_task") as send_task:
            affiliation = create_affiliation(
                self.identity, self.profile, changed_by="test", reference="9040"
            )
            self.assertEqual(
                OutboxEvent.objects.get(affiliation=affiliation).action, "PROVISION"
            )
            send_task.assert_called_once_with("provisioning.dispatch_outbox")

        pre.provisioning_action = ""
        pre.save(update_fields=["provisioning_action"])
        affiliation.refresh_from_db()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                transition_affiliation(
                    affiliation.pk, "ASP", changed_by="test", expected_from_code="PRE"
                )
                transition_affiliation(
                    affiliation.pk, "INGRE", changed_by="test", expected_from_code="ASP"
                )
                self.assertEqual(OutboxEvent.objects.filter(identity=self.identity).count(), 2)
                raise RuntimeError("force producer rollback")
        self.assertEqual(OutboxEvent.objects.filter(identity=self.identity).count(), 1)
        affiliation.refresh_from_db()
        self.assertEqual(affiliation.current_status.code, "PRE")

    def test_non_action_state_does_not_emit_and_seed_configures_fce_actions(self):
        """PRE and ASP stay local while INGRE, ALUMNO and PROFESOR:SI provision."""

        rows = {row.code: row for row in SagaStatusDefinition.objects.filter(
            profile_type__code="ALUMNO", code__in=["PRE", "ASP", "INGRE", "ALUMNO", "GRADUADO"]
        )}
        self.assertEqual(rows["PRE"].provisioning_action, "")
        self.assertEqual(rows["ASP"].provisioning_action, "")
        self.assertEqual(rows["INGRE"].provisioning_action, "PROVISION")
        self.assertTrue(rows["ALUMNO"].requires_m365_license)
        self.assertEqual(rows["GRADUADO"].provisioning_action, "SUSPEND")
        self.assertEqual(OutboxEvent.objects.count(), 0)

    def test_claim_commits_without_retaining_row_lock(self):
        """Claim timestamps survive and an independent connection can update the row."""

        event = self._event()
        self.assertEqual(event.status, OutboxEvent.Status.PENDING)
        self.assertEqual(claim_events(limit=1), [event.pk])
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxEvent.Status.PROCESSING)
        self.assertIsNotNone(event.locked_at)
        self.assertIsNotNone(event.heartbeat_at)
        self.assertFalse(connection.in_atomic_block)

        secondary = connection.copy(alias="provisioning_claim_probe")
        try:
            secondary.ensure_connection()
            secondary.set_autocommit(False)
            with secondary.cursor() as cursor:
                cursor.execute(
                    "UPDATE people_outbox_events SET last_error = %s WHERE id = %s",
                    ["independent connection write", event.pk],
                )
                updated = cursor.rowcount
            secondary.commit()
            self.assertEqual(updated, 1)
        finally:
            secondary.close()
        event.refresh_from_db()
        self.assertEqual(event.last_error, "independent connection write")

    def test_fifo_blocking_failed_blocking_and_parallel_identities(self):
        """Later events wait for earlier nonterminal rows, while different people proceed."""

        first = self._event()
        second = self._event()
        other_person = self._identity()
        parallel = self._event(identity=other_person)
        self.assertEqual(claim_events(limit=10), [first.pk, parallel.pk])
        first.status = OutboxEvent.Status.FAILED
        first.save(update_fields=["status"])
        self.assertEqual(claim_events(limit=10), [])
        first.status = OutboxEvent.Status.SUCCEEDED
        first.save(update_fields=["status"])
        self.assertEqual(claim_events(limit=10), [second.pk])

    @override_settings(LUCY_M365_SKU_ID="test-sku")
    def test_checkpoint_and_moodle_continuation_do_not_repeat_graph(self):
        """Graph/license checkpoints persist across the asynchronous Moodle boundary."""

        event = self._event(required=True)
        OutboxEvent.objects.filter(pk=event.pk).update(status=OutboxEvent.Status.PROCESSING)
        graph = mock.Mock()
        graph.get_user.return_value = None
        graph.create_user.return_value = GraphUserResult(
            "graph-id", event.payload["upn"], "Ada Lovelace", True
        )
        graph.assign_license.return_value = LicenseResult(event.payload["upn"], "test-sku", True)
        graph.set_temporary_password.return_value = SimpleNamespace(simulated=False)

        enqueue = mock.Mock()
        state = run_saga(event.pk, graph=graph, moodle=mock.Mock(), mailer=mock.Mock(), enqueue_moodle=enqueue)
        self.assertEqual(state, OutboxEvent.Status.WAITING_SUBTASK)
        enqueue.assert_called_once_with(event.pk)
        event.refresh_from_db()
        self.assertEqual(event.step_results["graph_user"]["status"], "CONFIRMED")
        self.assertEqual(event.step_results["license"]["status"], "CONFIRMED")

        moodle = mock.Mock()
        moodle.create_user.return_value = MoodleUserResult(88, event.payload["upn"], event.payload["upn"], True)
        moodle.get_course_by_shortname.return_value = {"id": 9040, "shortname": "9040"}
        moodle.enrol_user.return_value = MoodleEnrollmentResult(88, 9040, 5, True)
        self.assertEqual(run_moodle_step(event.pk, moodle=moodle), OutboxEvent.Status.PENDING)
        OutboxEvent.objects.filter(pk=event.pk).update(status=OutboxEvent.Status.PROCESSING)
        mailer = mock.Mock()
        mailer.send_credentials_email.return_value = MailDeliveryResult(
            event.payload["recipient"], "credentials", True
        )
        self.assertEqual(
            run_saga(event.pk, graph=graph, moodle=moodle, mailer=mailer),
            OutboxEvent.Status.SUCCEEDED,
        )
        graph.create_user.assert_called_once()
        graph.assign_license.assert_called_once()
        graph.set_temporary_password.assert_called_once()

    def test_preexisting_graph_account_skips_password_rotation(self):
        """Existing-account checkpoint must never invoke password or credential mail."""

        event = self._event(course="")
        event.status = OutboxEvent.Status.PROCESSING
        event.step_results = {
            "graph_user": {"status": "CONFIRMED", "simulated": False, "details": {"created": False}},
            "license": {"status": "SKIPPED_NOT_REQUIRED", "simulated": False, "details": {}},
            "moodle": {"status": "SKIPPED_NO_COURSE", "simulated": False, "details": {}},
        }
        event.save(update_fields=["status", "step_results"])
        graph, mailer = mock.Mock(), mock.Mock()
        result = run_saga(event.pk, graph=graph, moodle=mock.Mock(), mailer=mailer)
        self.assertEqual(result, OutboxEvent.Status.SUCCEEDED)
        graph.set_temporary_password.assert_not_called()
        mailer.send_credentials_email.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.step_results["password_and_mail"]["status"], "SKIPPED_EXISTING_USER")

    def test_smtp_retry_rotates_password_and_never_persists_it(self):
        """A transient mail error retries the combined step with a different ephemeral key."""

        event = self._event(course="")
        event.status = OutboxEvent.Status.PROCESSING
        event.step_results = {
            "graph_user": {"status": "CONFIRMED", "simulated": False, "details": {"created": True}},
            "license": {"status": "SKIPPED_NOT_REQUIRED", "simulated": False, "details": {}},
            "moodle": {"status": "SKIPPED_NO_COURSE", "simulated": False, "details": {}},
        }
        event.save(update_fields=["status", "step_results"])
        graph = mock.Mock()
        mailer = mock.Mock()
        mailer.send_credentials_email.side_effect = [
            MailDeliveryResult(event.payload["recipient"], "credentials", False, error_message="SMTPException: offline"),
            MailDeliveryResult(event.payload["recipient"], "credentials", True),
        ]
        self.assertEqual(
            run_saga(event.pk, graph=graph, moodle=mock.Mock(), mailer=mailer),
            OutboxEvent.Status.RETRY_SCHEDULED,
        )
        first_password = graph.set_temporary_password.call_args.kwargs["password"]
        OutboxEvent.objects.filter(pk=event.pk).update(status=OutboxEvent.Status.PROCESSING)
        self.assertEqual(
            run_saga(event.pk, graph=graph, moodle=mock.Mock(), mailer=mailer),
            OutboxEvent.Status.SUCCEEDED,
        )
        second_password = graph.set_temporary_password.call_args.kwargs["password"]
        self.assertNotEqual(first_password, second_password)
        event.refresh_from_db()
        self.assertNotIn(first_password, str(event.step_results) + event.last_error)
        self.assertNotIn(second_password, str(event.step_results) + event.last_error)

    def test_shared_license_is_not_removed_while_other_affiliation_requires_it(self):
        """A live license-required affiliation protects the identity-level SKU."""

        event = self._event(action="SUSPEND")
        event.status = OutboxEvent.Status.PROCESSING
        event.step_results = {"moodle": {"status": "CONFIRMED", "simulated": False, "details": {}}}
        event.save(update_fields=["status", "step_results"])
        active_status = SagaStatusDefinition.objects.get(profile_type=self.profile, code="ALUMNO")
        Affiliation.objects.create(
            identity=self.identity,
            profile_type=self.profile,
            current_status=active_status,
            reference="9041",
            is_active=True,
        )
        graph = mock.Mock()
        result = run_saga(event.pk, graph=graph, moodle=mock.Mock(), mailer=mock.Mock())
        self.assertEqual(result, OutboxEvent.Status.SUCCEEDED)
        graph.remove_license.assert_not_called()
        graph.disable_user.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.step_results["license"]["status"], "SKIPPED_STILL_REQUIRED")
        self.assertEqual(event.step_results["graph_disable"]["status"], "SKIPPED_STILL_ACTIVE")

    def test_rate_limit_does_not_increment_attempt_but_unavailable_does(self):
        """Typed rate limits honor Retry-After; transient failures consume attempts."""

        event = self._event()
        event.status = OutboxEvent.Status.PROCESSING
        event.save(update_fields=["status"])
        now = timezone.now()
        self.assertEqual(
            record_failure(event.pk, RateLimitExceededError("limited", retry_after=120)),
            OutboxEvent.Status.RETRY_SCHEDULED,
        )
        event.refresh_from_db()
        self.assertEqual(event.attempts, 0)
        self.assertGreaterEqual(event.retry_after, now + timedelta(seconds=119))
        OutboxEvent.objects.filter(pk=event.pk).update(status=OutboxEvent.Status.PROCESSING)
        record_failure(event.pk, RemoteServiceUnavailableError("offline"))
        event.refresh_from_db()
        self.assertEqual(event.attempts, 1)
        OutboxEvent.objects.filter(pk=event.pk).update(status=OutboxEvent.Status.PROCESSING)
        self.assertEqual(record_failure(event.pk, AuthenticationError("unauthorized")), OutboxEvent.Status.FAILED)

    def test_sweeper_rescues_stale_heartbeat_and_increments_attempt(self):
        """A stale processing event gets one bounded retry attempt."""

        event = self._event()
        old = timezone.now() - timedelta(minutes=6)
        OutboxEvent.objects.filter(pk=event.pk).update(
            status=OutboxEvent.Status.PROCESSING, heartbeat_at=old, locked_at=old
        )
        result = sweep_stuck_events(now=timezone.now())
        event.refresh_from_db()
        self.assertEqual(result["rescued"], 1)
        self.assertEqual(event.status, OutboxEvent.Status.RETRY_SCHEDULED)
        self.assertEqual(event.attempts, 1)

    def test_sweeper_recovers_waiting_subtask_and_marks_exhausted_event_failed(self):
        """Lost Moodle work uses the two-hour threshold and bounded attempt policy."""

        event = self._event()
        stale = timezone.now() - timedelta(hours=3)
        OutboxEvent.objects.filter(pk=event.pk).update(
            status=OutboxEvent.Status.WAITING_SUBTASK,
            heartbeat_at=stale,
            max_attempts=1,
        )
        result = sweep_stuck_events(now=timezone.now())
        event.refresh_from_db()
        self.assertEqual(result, {"rescued": 0, "failed": 1})
        self.assertEqual(event.status, OutboxEvent.Status.FAILED)
        self.assertEqual(event.attempts, 1)

    def test_unexpected_failure_is_retried_and_moodle_task_rate_is_configured(self):
        """Unexpected worker exceptions cannot strand PROCESSING and Moodle is rate-limited."""

        event = self._event()
        OutboxEvent.objects.filter(pk=event.pk).update(status=OutboxEvent.Status.PROCESSING)
        self.assertEqual(record_failure(event.pk, RuntimeError("unexpected")), OutboxEvent.Status.RETRY_SCHEDULED)
        event.refresh_from_db()
        self.assertEqual(event.attempts, 1)
        self.assertEqual(moodle_celery_task.rate_limit, "150/m")

    def test_rearm_simulated_events_supports_dry_run_and_commit_dispatch(self):
        """The command reports without writing in dry-run and resets checkpoints atomically."""

        event = self._event()
        OutboxEvent.objects.filter(pk=event.pk).update(
            status=OutboxEvent.Status.SIMULATED,
            step_results={"graph_user": {"status": "CONFIRMED"}},
            attempts=3,
        )
        with mock.patch("apps.provisioning.management.commands.rearm_simulated_events.current_app.send_task") as send_task:
            call_command("rearm_simulated_events", "--dry-run")
            event.refresh_from_db()
            self.assertEqual(event.status, OutboxEvent.Status.SIMULATED)
            self.assertEqual(event.attempts, 3)
            send_task.assert_not_called()

            call_command("rearm_simulated_events")
            event.refresh_from_db()
            self.assertEqual(event.status, OutboxEvent.Status.PENDING)
            self.assertEqual(event.step_results, {})
            self.assertEqual(event.attempts, 0)
            send_task.assert_called_once_with("provisioning.dispatch_outbox")

    @override_settings(LUCY_DRY_RUN=True, LUCY_M365_SKU_ID="test-sku")
    def test_dry_run_finishes_simulated_with_simulated_checkpoints(self):
        """Simulation traverses each adapter stage but never consumes the event as success."""

        event = self._event(required=True)
        OutboxEvent.objects.filter(pk=event.pk).update(status=OutboxEvent.Status.PROCESSING)
        graph = mock.Mock()
        graph.get_user.return_value = None
        graph.create_user.return_value = GraphUserResult("simulated", event.payload["upn"], "Ada", True, True)
        graph.assign_license.return_value = LicenseResult(event.payload["upn"], "sku", True, True)
        graph.set_temporary_password.return_value = SimpleNamespace(simulated=True)
        moodle = mock.Mock()
        moodle.create_user.return_value = MoodleUserResult(-1, event.payload["upn"], event.payload["upn"], True, True)
        moodle.get_course_by_shortname.return_value = {"id": 9040, "shortname": "9040"}
        moodle.enrol_user.return_value = MoodleEnrollmentResult(-1, 9040, 5, True, True)
        mailer = mock.Mock()
        mailer.send_credentials_email.return_value = MailDeliveryResult(
            event.payload["recipient"], "credentials", True, simulated=True
        )
        enqueue = mock.Mock()
        self.assertEqual(
            run_saga(event.pk, graph=graph, moodle=moodle, mailer=mailer, enqueue_moodle=enqueue),
            OutboxEvent.Status.WAITING_SUBTASK,
        )
        self.assertEqual(run_moodle_step(event.pk, moodle=moodle, dry_run=True), OutboxEvent.Status.PENDING)
        OutboxEvent.objects.filter(pk=event.pk).update(status=OutboxEvent.Status.PROCESSING)
        self.assertEqual(
            run_saga(event.pk, graph=graph, moodle=moodle, mailer=mailer, dry_run=True),
            OutboxEvent.Status.SIMULATED,
        )
        event.refresh_from_db()
        self.assertTrue(all(step["simulated"] for step in event.step_results.values()))
