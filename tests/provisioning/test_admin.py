"""Admin coverage for operational outbox inspection and actions."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from django.contrib import admin

from apps.people.admin import AffiliationHistoryInline, AffiliationInline, OutboxEventInline
from apps.people.models import (
    Affiliation,
    Identity,
    OutboxEvent,
    ProfileTypeDefinition,
    SagaStatusDefinition,
)
from apps.provisioning.admin import OutboxEventAdmin


class OutboxAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin-test", email="admin@example.test", password="secret"
        )
        self.client.force_login(self.user)
        suffix = uuid4().hex[:10]
        self.identity = Identity.objects.create(
            category=Identity.Category.FISICA,
            identifier=f"dni-{suffix}",
            first_name="Ada",
            last_name="Lovelace",
            institutional_upn=f"ada-{suffix}@eco.unrc.edu.ar",
        )
        self.profile = ProfileTypeDefinition.objects.create(code=f"P{suffix}", name="Perfil")
        self.status = SagaStatusDefinition.objects.create(
            profile_type=self.profile, code="ACT", name="Activo", is_initial=True
        )
        self.affiliation = Affiliation.objects.create(
            identity=self.identity,
            profile_type=self.profile,
            current_status=self.status,
        )

    def create_event(self, status):
        return OutboxEvent.objects.create(
            action="PROVISION",
            identity=self.identity,
            affiliation=self.affiliation,
            payload={"test": "value"},
            status=status,
            attempts=3,
            max_attempts=9,
            last_error="previous failure",
        )

    def test_outbox_changelist_renders_event_and_status_badges(self):
        events = [
            self.create_event(OutboxEvent.Status.FAILED),
            self.create_event(OutboxEvent.Status.SIMULATED),
            self.create_event(OutboxEvent.Status.SUCCEEDED),
        ]

        response = self.client.get(reverse("admin:people_outboxevent_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada Lovelace")
        for event in events:
            self.assertContains(response, str(event.public_id))
            self.assertContains(response, event.status)

    def test_rearm_action_handles_failed_and_simulated_events(self):
        events = [
            self.create_event(OutboxEvent.Status.FAILED),
            self.create_event(OutboxEvent.Status.SIMULATED),
        ]

        response = self.client.post(
            reverse("admin:people_outboxevent_changelist"),
            {
                "action": "rearm_selected_events",
                "_selected_action": [str(event.pk) for event in events],
                "index": "0",
            },
        )

        self.assertEqual(response.status_code, 302)
        for event in events:
            event.refresh_from_db()
            self.assertEqual(event.status, OutboxEvent.Status.PENDING)
            self.assertEqual(event.attempts, 0)
            self.assertEqual(event.max_attempts, 9)
            self.assertEqual(event.last_error, "")

    def test_outbox_queryset_loads_display_relations_without_n_plus_one(self):
        self.create_event(OutboxEvent.Status.PENDING)
        request = RequestFactory().get("/admin/people/outboxevent/")
        request.user = self.user

        with self.assertNumQueries(1):
            events = list(OutboxEventAdmin(OutboxEvent, admin.site).get_queryset(request))
            [(event.identity.first_name, event.affiliation.current_status.name) for event in events]

    def test_affiliation_inlines_are_fully_read_only(self):
        request = RequestFactory().get("/admin/")
        request.user = self.user
        inlines = (
            AffiliationInline(Identity, admin.site),
            AffiliationHistoryInline(Affiliation, admin.site),
            OutboxEventInline(Affiliation, admin.site),
        )

        for inline in inlines:
            self.assertFalse(inline.has_add_permission(request))
            self.assertFalse(inline.has_change_permission(request))
            self.assertFalse(inline.has_delete_permission(request))

    def test_non_superuser_cannot_access_critical_configuration(self):
        staff = get_user_model().objects.create_user(
            username="ordinary-staff", password="secret", is_staff=True
        )
        self.client.force_login(staff)

        response = self.client.get(reverse("admin:people_profiletypedefinition_changelist"))

        self.assertEqual(response.status_code, 403)
