"""PostgreSQL-backed coverage for dashboard rendering and registration flows."""

from django.test import TestCase
from django.urls import reverse

from apps.people.models import (
    Affiliation,
    Identity,
    OutboxEvent,
    ProfileTypeDefinition,
    SagaStatusDefinition,
)


class PortalDashboardTests(TestCase):
    def setUp(self):
        self.posgrado = ProfileTypeDefinition.objects.get(code="POSGRADO")
        self.extension = ProfileTypeDefinition.objects.get(code="EXTENSION")

    def registration_data(self, dni="30123456", **overrides):
        data = {
            "dni": dni,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "personal_email": "ada@example.test",
            "program": "Maestría en Sistemas",
            "course": "Diplomatura en Datos",
        }
        data.update(overrides)
        return data

    def test_all_dashboard_urls_render_for_anonymous_visitors(self):
        for route in (
            "portal:administracion",
            "portal:postgrado",
            "portal:extension",
        ):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)

    def test_administration_htmx_search_returns_results_fragment(self):
        identity = Identity.objects.create(
            category=Identity.Category.FISICA,
            identifier="27123456",
            first_name="Grace",
            last_name="Hopper",
            institutional_upn="grace.hopper@eco.unrc.edu.ar",
        )
        profile = ProfileTypeDefinition.objects.get(code="ALUMNO")
        status = SagaStatusDefinition.objects.get(profile_type=profile, is_initial=True)
        Affiliation.objects.create(
            identity=identity,
            profile_type=profile,
            current_status=status,
            reference="Licenciatura en Economía",
        )

        response = self.client.get(
            reverse("portal:administracion"),
            {"q": "Hopper"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grace Hopper")
        self.assertContains(response, "27123456")
        self.assertContains(response, "grace.hopper@eco.unrc.edu.ar")
        self.assertNotContains(response, "<html")

    def assert_registration(self, *, route, profile, reference_key, reference):
        response = self.client.post(
            reverse(route),
            self.registration_data(**{reference_key: reference}),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inscripción registrada correctamente")
        self.assertContains(response, "hx-swap-oob")

        identity = Identity.objects.get(identifier="30123456")
        self.assertEqual(identity.first_name, "Ada")
        self.assertTrue(identity.affiliations.filter(profile_type=profile, reference=reference).exists())
        affiliation = identity.affiliations.get(profile_type=profile, reference=reference)
        self.assertEqual(affiliation.current_status, profile.initial_status)
        event = OutboxEvent.objects.get(affiliation=affiliation)
        self.assertEqual(event.status, OutboxEvent.Status.PENDING)
        self.assertEqual(event.action, "PROVISION")
        self.assertEqual(event.payload["course_shortname"], reference)
        return identity

    def test_postgrado_post_creates_affiliation_and_pending_outbox(self):
        self.assert_registration(
            route="portal:postgrado",
            profile=self.posgrado,
            reference_key="program",
            reference="Maestría en Sistemas",
        )

    def test_extension_post_creates_affiliation_and_pending_outbox(self):
        self.assert_registration(
            route="portal:extension",
            profile=self.extension,
            reference_key="course",
            reference="Diplomatura en Datos",
        )

    def test_second_dashboard_registration_reuses_identity(self):
        first_identity = self.assert_registration(
            route="portal:postgrado",
            profile=self.posgrado,
            reference_key="program",
            reference="Maestría en Sistemas",
        )
        second_identity = self.assert_registration(
            route="portal:extension",
            profile=self.extension,
            reference_key="course",
            reference="Diplomatura en Datos",
        )

        self.assertEqual(first_identity.pk, second_identity.pk)
        self.assertEqual(Identity.objects.filter(identifier="30123456").count(), 1)
        self.assertEqual(OutboxEvent.objects.filter(identity=first_identity).count(), 2)

    def test_invalid_registration_returns_error_fragment_without_writes(self):
        response = self.client.post(
            reverse("portal:postgrado"),
            self.registration_data(dni="DNI-invalido", first_name=""),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No pudimos registrar la inscripción")
        self.assertContains(response, "DNI válido")
        self.assertEqual(Identity.objects.count(), 0)
        self.assertEqual(OutboxEvent.objects.count(), 0)

    def test_missing_required_fields_rerender_full_form_with_client_error(self):
        response = self.client.post(reverse("portal:extension"), {})

        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "DNI", status_code=422)
        self.assertEqual(Identity.objects.count(), 0)
        self.assertEqual(OutboxEvent.objects.count(), 0)
