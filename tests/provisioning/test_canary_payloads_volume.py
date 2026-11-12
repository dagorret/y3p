"""Prueba controlada de volumen: 100 ingresos desde 10 perfiles canarios.

Usa los servicios reales de dominio (``create_affiliation``,
``transition_affiliation``, ``process_expired_affiliations``) y el orquestador
real de ``apps.provisioning`` con los clientes reales de Graph, Moodle y SMTP
montados sobre transportes instrumentados que capturan el payload exacto
inmediatamente antes del transporte (HTTP/SMTP). Ninguna llamada sale a la red.
"""

import copy
import json
import logging
import re
import smtplib
from collections import Counter, deque
from datetime import timedelta
from urllib.parse import unquote

import requests
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.integrations.services.graph import MicrosoftGraphClient
from apps.integrations.services.mailer import MailerClient
from apps.integrations.services.moodle import MoodleRestClient
from apps.people.models import (
    Affiliation,
    AffiliationStatusHistory,
    Identity,
    OutboxEvent,
    PersonalProfile,
    ProfileTypeDefinition,
    SagaStatusDefinition,
    SagaTransitionRule,
)
from apps.people.services import create_affiliation, transition_affiliation
from apps.people.ttl import process_expired_affiliations
from apps.provisioning.dispatcher import claim_events
from apps.provisioning.orchestrator import run_moodle_step, run_saga
from unittest.mock import patch

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MOODLE_BASE = "https://moodle.eco.test"
SKU_A1 = "sku-a1-canario-314d77d9"


class FakeResponse:
    """Respuesta mínima compatible con ``requests.Response`` para los clientes."""

    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self):
        return copy.deepcopy(self._body)


class GraphFakeTransport:
    """Backend Entra/Graph con estado; captura cada solicitud antes de aplicarla."""

    def __init__(self, capture):
        self.capture = capture
        self.users = {}
        self._sequence = 0

    def seed_existing_user(self, upn, display_name):
        self._sequence += 1
        self.users[upn] = {
            "id": f"azure-preexistente-{self._sequence}",
            "displayName": display_name,
            "accountEnabled": True,
            "sku": None,
            "password_patches": [],
            "force_change": False,
        }

    def _record(self, method, url, headers, body):
        self.capture.append({
            "service": "microsoft_graph",
            "method": method,
            "url": url,
            "headers": copy.deepcopy(headers or {}),
            "body": copy.deepcopy(body),
        })

    def _path(self, url):
        return url[len(GRAPH_BASE) + 1:]

    def post(self, url, data=None, json=None, headers=None, timeout=None, params=None):
        if "oauth2/v2.0/token" in url:
            self._record("POST", url, headers, {"form": dict(data)})
            return FakeResponse(200, {"access_token": "token-canario", "expires_in": 3600})
        self._record("POST", url, headers, json)
        path = self._path(url)
        if path == "users":
            upn = json["userPrincipalName"]
            if upn in self.users:
                return FakeResponse(409, {"error": {"code": "Request_ResourceAlreadyExists"}})
            self._sequence += 1
            self.users[upn] = {
                "id": f"azure-{self._sequence:04d}",
                "displayName": json["displayName"],
                "accountEnabled": True,
                "sku": None,
                "password_patches": [],
                "force_change": json["passwordProfile"]["forceChangePasswordNextSignIn"],
            }
            return FakeResponse(201, {
                "id": self.users[upn]["id"],
                "userPrincipalName": upn,
                "displayName": json["displayName"],
            })
        if path.endswith("/assignLicense"):
            upn = unquote(path.split("/")[1])
            user = self.users.get(upn)
            if user is None:
                return FakeResponse(404, {"error": {"code": "Request_ResourceNotFound"}})
            for item in json.get("addLicenses", []):
                user["sku"] = item["skuId"]
            for sku in json.get("removeLicenses", []):
                if user["sku"] == sku:
                    user["sku"] = None
            return FakeResponse(200, {"id": user["id"]})
        raise AssertionError(f"POST Graph no esperado: {path}")

    def get(self, url, params=None, headers=None, timeout=None, json=None):
        self._record("GET", url, headers, {"params": copy.deepcopy(params)})
        upn = unquote(self._path(url).split("/")[1])
        user = self.users.get(upn)
        if user is None:
            return FakeResponse(404, {"error": {"code": "Request_ResourceNotFound"}})
        return FakeResponse(200, {
            "id": user["id"], "userPrincipalName": upn, "displayName": user["displayName"],
        })

    def patch(self, url, json=None, headers=None, timeout=None, params=None):
        self._record("PATCH", url, headers, json)
        upn = unquote(self._path(url).split("/")[1])
        user = self.users.get(upn)
        if user is None:
            return FakeResponse(404, {"error": {"code": "Request_ResourceNotFound"}})
        if "accountEnabled" in json:
            user["accountEnabled"] = json["accountEnabled"]
        profile = json.get("passwordProfile")
        if profile:
            user["password_patches"].append(profile["password"])
            user["force_change"] = profile["forceChangePasswordNextSignIn"]
        return FakeResponse(204, None)


class MoodleFakeTransport:
    """Backend Moodle REST con estado y caída transitoria configurable."""

    def __init__(self, capture, courses):
        self.capture = capture
        self.courses = dict(courses)
        self.users = {}
        self.enrollments = set()
        self.enrol_calls = []
        self.unenrol_calls = []
        self.fail_once_usernames = set()
        self._sequence = 0

    def post(self, url, data=None, timeout=None, json=None, headers=None):
        self.capture.append({
            "service": "moodle_rest",
            "method": "POST",
            "url": url,
            "body": copy.deepcopy(data),
        })
        function = data["wsfunction"]
        if function == "core_user_get_users_by_field":
            username = data["values[0]"]
            if username in self.fail_once_usernames:
                self.fail_once_usernames.discard(username)
                raise requests.ConnectionError("Caída transitoria simulada de Moodle")
            user = self.users.get(username)
            body = [{"id": user["id"], "username": username, "email": user["email"]}] if user else []
            return FakeResponse(200, body)
        if function == "core_user_create_users":
            username = data["users[0][username]"]
            if username in self.users:
                return FakeResponse(200, {"exception": "moodle_exception", "errorcode": "usernameexists"})
            self._sequence += 1
            self.users[username] = {
                "id": self._sequence,
                "email": data["users[0][email]"],
                "firstname": data["users[0][firstname]"],
                "lastname": data["users[0][lastname]"],
                "auth": data["users[0][auth]"],
            }
            return FakeResponse(200, [{"id": self._sequence, "username": username}])
        if function == "core_course_get_courses_by_field":
            shortname = data["value"]
            course_id = self.courses.get(shortname)
            courses = [{"id": course_id, "shortname": shortname}] if course_id else []
            return FakeResponse(200, {"courses": courses})
        if function == "enrol_manual_enrol_users":
            key = (data["enrolments[0][userid]"], data["enrolments[0][courseid]"], data["enrolments[0][roleid]"])
            self.enrol_calls.append(key)
            self.enrollments.add(key)
            return FakeResponse(200, None)
        if function == "enrol_manual_unenrol_users":
            key = (data["enrolments[0][userid]"], data["enrolments[0][courseid]"], data["enrolments[0][roleid]"])
            self.unenrol_calls.append(key)
            self.enrollments.discard(key)
            return FakeResponse(200, None)
        raise AssertionError(f"Función Moodle no esperada: {function}")


class SmtpFakeServer:
    """Servidor SMTP instrumentado: captura el mensaje exacto antes de entregar."""

    def __init__(self, outbox, host, port, timeout=None):
        self.outbox = outbox
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        self.outbox.starttls_count += 1

    def login(self, username, password):
        self.outbox.logins.append(username)

    def send_message(self, message):
        record = {
            "from": message["From"],
            "to": message["To"],
            "subject": message["Subject"],
            "message_id": message["Message-ID"],
            "text_body": message.get_body(preferencelist=("plain",)).get_content(),
        }
        self.outbox.attempts.append(record)
        if record["to"] in self.outbox.fail_once_recipients:
            self.outbox.fail_once_recipients.discard(record["to"])
            raise smtplib.SMTPServerDisconnected("Corte SMTP simulado")
        self.outbox.delivered.append(record)
        return {}


class SmtpFakeOutbox:
    def __init__(self):
        self.attempts = []
        self.delivered = []
        self.logins = []
        self.starttls_count = 0
        self.fail_once_recipients = set()

    def factory(self, host, port, timeout=None):
        return SmtpFakeServer(self, host, port, timeout=timeout)


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(f"{record.getMessage()} | {record.__dict__!r}")


@override_settings(LUCY_DRY_RUN=False, LUCY_M365_SKU_ID=SKU_A1)
class CanaryPayloadVolumeTests(TransactionTestCase):
    """100 ingresos reales (10 perfiles x 10 casos) contra adaptadores instrumentados."""

    maxDiff = None

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("La prueba de volumen requiere PostgreSQL.")
        self._seed_catalogs()
        # Ningún camino puede publicar al broker ni tocar red o SMTP reales.
        self.send_task = self.enterContext(patch("apps.people.services.current_app.send_task"))
        self.enterContext(patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("Red HTTP real no permitida"),
        ))
        self.enterContext(patch("smtplib.SMTP", side_effect=AssertionError("SMTP real no permitido")))
        self.enterContext(patch("smtplib.SMTP_SSL", side_effect=AssertionError("SMTP SSL real no permitido")))
        # El pacing institucional (0.4s por llamada) no aplica al transporte fake.
        self.enterContext(patch.object(MoodleRestClient, "_pace_request", lambda client: None))
        self.log_capture = _LogCapture()
        logging.getLogger().addHandler(self.log_capture)
        self.addCleanup(logging.getLogger().removeHandler, self.log_capture)

    # ------------------------------------------------------------------ setup
    def _seed_catalogs(self):
        """Restituye el grafo necesario (TransactionTestCase trunca catálogos)."""

        def status(profile, code, **defaults):
            obj, _ = SagaStatusDefinition.objects.update_or_create(
                profile_type=profile, code=code, defaults=defaults,
            )
            return obj

        def rule(profile, source, target, action, *, expiration=False):
            obj, _ = SagaTransitionRule.objects.update_or_create(
                profile_type=profile, from_status=source, to_status=target,
                defaults={"action_name": action, "is_expiration": expiration,
                          "requires_cross_validation": False},
            )
            return obj

        self.grado, _ = ProfileTypeDefinition.objects.update_or_create(
            code="ALUMNO", defaults={"name": "Alumno", "allows_multiple_instances": True},
        )
        SagaStatusDefinition.objects.filter(profile_type=self.grado).update(is_initial=False)
        pre = status(self.grado, "PRE", name="Preinscripto", is_initial=True, is_terminal=False,
                     default_ttl_days=60, provisioning_action="",
                     requires_collaboration_license=False)
        alumno = status(self.grado, "ALUMNO", name="Alumno regular", is_initial=False,
                        is_terminal=False, default_ttl_days=None,
                        provisioning_action="PROVISION", requires_collaboration_license=True)
        baja_def = status(self.grado, "BAJA_DEF", name="Baja definitiva", is_initial=False,
                          is_terminal=True, default_ttl_days=None,
                          provisioning_action="TERMINATE", requires_collaboration_license=False)
        rule(self.grado, pre, alumno, "Confirmación académica")
        rule(self.grado, alumno, baja_def, "Baja definitiva")

        self.posgrado, _ = ProfileTypeDefinition.objects.update_or_create(
            code="POSGRADO", defaults={"name": "Posgrado", "allows_multiple_instances": True},
        )
        SagaStatusDefinition.objects.filter(profile_type=self.posgrado).update(is_initial=False)
        status(self.posgrado, "ALUMNO", name="Alumno de posgrado", is_initial=True,
               is_terminal=False, default_ttl_days=365, provisioning_action="PROVISION",
               requires_collaboration_license=True)

        self.extension, _ = ProfileTypeDefinition.objects.update_or_create(
            code="EXTENSION", defaults={"name": "Extensión", "allows_multiple_instances": True},
        )
        SagaStatusDefinition.objects.filter(profile_type=self.extension).update(is_initial=False)
        inscripto = status(self.extension, "INSCRIPTO", name="Inscripto", is_initial=True,
                           is_terminal=False, default_ttl_days=180,
                           provisioning_action="PROVISION", requires_collaboration_license=True)
        baja_ext = status(self.extension, "BAJA", name="Vinculación vencida", is_initial=False,
                          is_terminal=True, default_ttl_days=None,
                          provisioning_action="SUSPEND", requires_collaboration_license=False)
        SagaTransitionRule.objects.filter(from_status=inscripto, is_expiration=True).delete()
        rule(self.extension, inscripto, baja_ext, "Vencimiento automático", expiration=True)

    def _person(self, dni, first_name, last_name):
        identity = Identity.objects.create(
            category=Identity.Category.FISICA,
            identifier=dni,
            first_name=first_name,
            last_name=last_name,
            institutional_upn=f"a{dni}@eco.unrc.edu.ar",
        )
        PersonalProfile.objects.create(identity=identity, personal_email=f"canario.{dni}@gmail.com")
        return identity

    def _alta_grado(self, identity, reference):
        affiliation = create_affiliation(
            identity, self.grado, changed_by="canario:ingesta", reason="Alta canario",
            reference=reference, modality="PRESENCIAL",
        )
        return transition_affiliation(
            affiliation.pk, "ALUMNO", changed_by="canario:ingesta",
            reason="Confirmación académica canario",
        )

    def _drain_outbox(self, graph, moodle, mailer, *, max_passes=15):
        moodle_queue = deque()
        passes = 0
        unfinished = OutboxEvent.objects.exclude(status__in=[
            OutboxEvent.Status.SUCCEEDED, OutboxEvent.Status.SIMULATED, OutboxEvent.Status.FAILED,
        ])
        while unfinished.exists():
            passes += 1
            self.assertLessEqual(passes, max_passes, "El Outbox no converge.")
            OutboxEvent.objects.filter(status=OutboxEvent.Status.RETRY_SCHEDULED).update(
                retry_after=timezone.now() - timedelta(seconds=1),
            )
            claimed = claim_events(limit=200)
            for event_id in claimed:
                result = run_saga(event_id, graph=graph, moodle=moodle, mailer=mailer,
                                  dry_run=False, enqueue_moodle=moodle_queue.append)
                self.assertIn(result, (
                    OutboxEvent.Status.WAITING_SUBTASK,
                    OutboxEvent.Status.SUCCEEDED,
                    OutboxEvent.Status.RETRY_SCHEDULED,
                ))
            while moodle_queue:
                run_moodle_step(moodle_queue.popleft(), moodle=moodle, dry_run=False)
            self.assertTrue(
                claimed or not unfinished.exists(),
                "Quedan eventos sin resolver pero ninguno es reclamable.",
            )
        self.assertFalse(OutboxEvent.objects.filter(status=OutboxEvent.Status.FAILED).exists())
        return passes

    # ------------------------------------------------------------------- test
    def test_100_canary_ingresos_payloads_and_accounting(self):
        capture = []
        graph_transport = GraphFakeTransport(capture)
        moodle_transport = MoodleFakeTransport(capture, courses={
            "2": 101, "3": 102, "MBA-FIN-2026": 201,
            "EXT-DIPLO-DATOS": 301, "EXT-TALLER-VENC": 302,
        })
        smtp_outbox = SmtpFakeOutbox()

        graph = MicrosoftGraphClient(
            tenant_id="tenant-canario", client_id="app-canario",
            client_secret="client-secret-canario", dry_run=False,
            session=graph_transport, graph_base_url=GRAPH_BASE,
        )
        moodle = MoodleRestClient(
            base_url=MOODLE_BASE, token="wstoken-canario", dry_run=False,
            session=moodle_transport,
        )
        mailer = MailerClient(
            host="smtp.eco.test", port=587, username="svc.cuentas",
            password="smtp-secret-canario", from_email="cuentas@eco.unrc.edu.ar",
            dry_run=False, smtp_factory=smtp_outbox.factory,
        )

        dnis = [f"990000{n:02d}" for n in range(1, 101)]
        block = lambda i: dnis[(i - 1) * 10: i * 10]
        upn = lambda dni: f"a{dni}@eco.unrc.edu.ar"

        # ---------------- Fase 1: ingesta de los 100 registros canarios.
        affiliations = {}
        for dni in block(1):
            affiliations[dni] = [self._alta_grado(self._person(dni, "Lucía", f"Alumna {dni[-2:]}"), "2")]
        for dni in block(2):
            identity = self._person(dni, "María José", "Núñez D'Angelo")
            affiliations[dni] = [self._alta_grado(identity, "2")]
        for dni in block(3):
            identity = self._person(dni, "Pedro", f"Preexistente {dni[-2:]}")
            graph_transport.seed_existing_user(upn(dni), f"Pedro Preexistente {dni[-2:]}")
            affiliations[dni] = [self._alta_grado(identity, "2")]
        for dni in block(4):
            identity = self._person(dni, "Carla", f"Doble Carrera {dni[-2:]}")
            affiliations[dni] = [self._alta_grado(identity, "2"), self._alta_grado(identity, "3")]
        for dni in block(5):
            identity = self._person(dni, "Franco", f"Reintento Moodle {dni[-2:]}")
            moodle_transport.fail_once_usernames.add(upn(dni))
            affiliations[dni] = [self._alta_grado(identity, "2")]
        for dni in block(6):
            identity = self._person(dni, "Ana", f"Posgrado {dni[-2:]}")
            affiliations[dni] = [create_affiliation(
                identity, self.posgrado, changed_by="canario:posgrado",
                reason="Inscripción cohorte 2026", reference="MBA-FIN-2026", modality="DISTANCIA",
            )]
        for dni in block(7):
            identity = self._person(dni, "Diego", f"Extensión {dni[-2:]}")
            affiliations[dni] = [create_affiliation(
                identity, self.extension, changed_by="canario:extension",
                reason="Inscripción extensión", reference="EXT-DIPLO-DATOS", modality="DISTANCIA",
            )]
        for dni in block(8):
            identity = self._person(dni, "Elena", f"Vencimiento {dni[-2:]}")
            affiliations[dni] = [create_affiliation(
                identity, self.extension, changed_by="canario:extension",
                reason="Inscripción taller", reference="EXT-TALLER-VENC", modality="DISTANCIA",
            )]
        for dni in block(9):
            affiliations[dni] = [self._alta_grado(self._person(dni, "Hugo", f"Baja {dni[-2:]}"), "2")]
        for dni in block(10):
            identity = self._person(dni, "Iris", f"Rearme SMTP {dni[-2:]}")
            smtp_outbox.fail_once_recipients.add(f"canario.{dni}@gmail.com")
            affiliations[dni] = [self._alta_grado(identity, "2")]

        self.assertEqual(Identity.objects.count(), 100)
        self.assertEqual(Affiliation.objects.count(), 110)
        self.assertEqual(OutboxEvent.objects.filter(action="PROVISION").count(), 110)

        provision_passes = self._drain_outbox(graph, moodle, mailer)

        # ---------------- Fase 2: vencimiento por TTL (perfil 8).
        self.assertEqual(Affiliation.objects.filter(
            identity__identifier__in=block(8),
        ).update(expires_at=timezone.now() - timedelta(days=1)), 10)
        sweep = process_expired_affiliations(limit=500)
        self.assertEqual(sweep, {"expired": 10, "errors": 0, "skipped": 0, "orphans": 0})
        suspend_passes = self._drain_outbox(graph, moodle, mailer)

        # ---------------- Fase 3: baja definitiva TERMINATE (perfil 9).
        for dni in block(9):
            transition_affiliation(
                affiliations[dni][0].pk, "BAJA_DEF",
                changed_by="canario:bedelia", reason="Baja definitiva solicitada",
            )
        terminate_passes = self._drain_outbox(graph, moodle, mailer)
        second_sweep = process_expired_affiliations(limit=500)
        self.assertEqual(second_sweep["orphans"], 0)

        # =============== CONTABILIDAD EXACTA ===============
        events = list(OutboxEvent.objects.all())
        self.assertEqual(len(events), 130)
        self.assertEqual(Counter(event.action for event in events),
                         {"PROVISION": 110, "SUSPEND": 10, "TERMINATE": 10})
        for event in events:
            self.assertEqual(event.status, OutboxEvent.Status.SUCCEEDED)
            self.assertIsNone(event.locked_at)
            self.assertIsNotNone(event.processed_at)
            self.assertFalse(any(step.get("simulated") for step in event.step_results.values()))
        self.assertEqual(AffiliationStatusHistory.objects.count(), 210)

        graph_creates = [r for r in capture if r["service"] == "microsoft_graph"
                         and r["method"] == "POST" and r["url"] == f"{GRAPH_BASE}/users"]
        license_posts = [r for r in capture if r["url"].endswith("/assignLicense")]
        license_adds = [r for r in license_posts if r["body"]["addLicenses"]]
        license_removals = [r for r in license_posts if r["body"]["removeLicenses"]]
        password_patches = [r for r in capture if r["method"] == "PATCH"
                            and "passwordProfile" in (r["body"] or {})]
        disable_patches = [r for r in capture if r["method"] == "PATCH"
                           and (r["body"] or {}).get("accountEnabled") is False]

        self.assertEqual(len(graph_creates), 90)
        self.assertEqual(len(graph_transport.users), 100)
        self.assertEqual(len(license_adds), 110)
        self.assertEqual(len(license_removals), 20)
        self.assertEqual(len(password_patches), 100)
        self.assertEqual(len(disable_patches), 20)
        self.assertEqual(sum(1 for u in graph_transport.users.values() if u["sku"] == SKU_A1), 80)
        self.assertEqual(sum(1 for u in graph_transport.users.values() if not u["accountEnabled"]), 20)

        reused_accounts = sum(
            1 for event in events
            if event.step_results.get("graph_user", {}).get("details", {}).get("created") is False
        )
        self.assertEqual(reused_accounts, 20)
        skipped_mail = sum(
            1 for event in events
            if event.step_results.get("password_and_mail", {}).get("status") == "SKIPPED_EXISTING_USER"
        )
        self.assertEqual(skipped_mail, 20)

        self.assertEqual(len(moodle_transport.users), 100)
        self.assertEqual(len(moodle_transport.enrol_calls), 110)
        self.assertEqual(len(moodle_transport.unenrol_calls), 20)
        self.assertEqual(len(set(moodle_transport.enrol_calls)), 110,
                         "Hubo matrículas duplicadas en Moodle.")
        self.assertEqual(len(moodle_transport.enrollments), 90)

        self.assertEqual(len(smtp_outbox.attempts), 100)
        self.assertEqual(len(smtp_outbox.delivered), 90)
        self.assertEqual(smtp_outbox.starttls_count, 100)
        delivered_recipients = Counter(r["to"] for r in smtp_outbox.delivered)
        for dni in block(3):
            self.assertNotIn(f"canario.{dni}@gmail.com", delivered_recipients)
        for dni in block(4) + block(10):
            self.assertEqual(delivered_recipients[f"canario.{dni}@gmail.com"], 1)

        # Perfil 3: conserva su clave (0 PATCH de contraseña) y no recibe correo.
        for dni in block(3):
            user = graph_transport.users[upn(dni)]
            self.assertEqual(user["password_patches"], [])
            self.assertFalse(user["force_change"])

        # Perfil 4: una sola identidad Teams, doble matrícula Moodle (cursos 2 y 3).
        for dni in block(4):
            self.assertEqual(Affiliation.objects.filter(identity__identifier=dni).count(), 2)
            moodle_id = moodle_transport.users[upn(dni)]["id"]
            self.assertEqual(
                {course for (user_id, course, _) in moodle_transport.enrollments if user_id == moodle_id},
                {101, 102},
            )
            self.assertEqual(len(graph_transport.users[upn(dni)]["password_patches"]), 1)

        # Perfil 5: pasó por WAITING_SUBTASK, reintentó Moodle y completó clave+correo.
        self.assertEqual(moodle_transport.fail_once_usernames, set())
        for dni in block(5):
            event = OutboxEvent.objects.get(identity__identifier=dni)
            self.assertEqual(event.attempts, 1)
            self.assertEqual(event.step_results["moodle"]["status"], "CONFIRMED")
            self.assertEqual(event.step_results["password_and_mail"]["status"], "CONFIRMED")

        # Perfil 6: bienvenida explícita de posgrado.
        for dni in block(6):
            event = OutboxEvent.objects.get(identity__identifier=dni)
            self.assertTrue(event.payload["send_welcome"])
            self.assertEqual(event.payload["modality"], "DISTANCIA")

        # Perfil 7: TTL de extensión calculado (180 días).
        for dni in block(7):
            affiliation = Affiliation.objects.get(identity__identifier=dni)
            remaining = affiliation.expires_at - timezone.now()
            self.assertTrue(timedelta(days=179) < remaining < timedelta(days=181))

        # Perfiles 8 y 9: retiro de licencia, desmatriculación y cuenta deshabilitada.
        for dni in block(8) + block(9):
            affiliation = Affiliation.objects.get(identity__identifier=dni)
            self.assertFalse(affiliation.is_active)
            user = graph_transport.users[upn(dni)]
            self.assertIsNone(user["sku"])
            self.assertFalse(user["accountEnabled"])
            moodle_id = moodle_transport.users[upn(dni)]["id"]
            self.assertFalse(any(u == moodle_id for (u, _, _) in moodle_transport.enrollments))
        for dni in block(9):
            event = OutboxEvent.objects.get(identity__identifier=dni, action="TERMINATE")
            self.assertEqual(event.step_results["graph_delete"]["status"], "SKIPPED_POLICY")

        # Perfil 10: rearme SMTP con clave regenerada vía PATCH, sin duplicados.
        for dni in block(10):
            patches = graph_transport.users[upn(dni)]["password_patches"]
            self.assertEqual(len(patches), 2)
            self.assertNotEqual(patches[0], patches[1])
            event = OutboxEvent.objects.get(identity__identifier=dni)
            self.assertEqual(event.attempts, 1)
            self.assertEqual(
                sum(1 for r in graph_creates if r["body"]["userPrincipalName"] == upn(dni)), 1)
            moodle_id = moodle_transport.users[upn(dni)]["id"]
            self.assertEqual(
                sum(1 for (u, _, _) in moodle_transport.enrol_calls if u == moodle_id), 1)
            delivered_body = next(r for r in smtp_outbox.delivered
                                  if r["to"] == f"canario.{dni}@gmail.com")["text_body"]
            self.assertIn(patches[1], delivered_body)
            self.assertNotIn(patches[0], delivered_body)

        # =============== FUGA DE SECRETOS: PostgreSQL y logs ===============
        secrets = set()
        for request_record in graph_creates + password_patches:
            secrets.add(request_record["body"]["passwordProfile"]["password"])
        for attempt in smtp_outbox.attempts:
            match = re.search(r"Contraseña temporal: (\S+)", attempt["text_body"])
            self.assertIsNotNone(match)
            secrets.add(match[1])
        self.assertEqual(len(secrets), 190)  # 90 bootstrap + 100 temporales (10 regeneradas)

        database_blob = []
        with connection.cursor() as cursor:
            for table in connection.introspection.table_names():
                cursor.execute(f'SELECT t::text FROM "{table}" t')
                database_blob.extend(row[0] for row in cursor.fetchall() if row[0])
        database_text = "\n".join(database_blob)
        log_text = "\n".join(self.log_capture.lines)
        for secret in secrets:
            self.assertNotIn(secret, database_text, "Contraseña temporal persistida en PostgreSQL.")
            self.assertNotIn(secret, log_text, "Contraseña temporal registrada en logs.")
        self.assertNotIn("smtp-secret-canario", database_text)
        self.assertNotIn("client-secret-canario", database_text)

        # =============== REPORTE ===============
        accounting = {
            "ingresos_registrados": {"identidades": 100, "afiliaciones": 110,
                                     "eventos_outbox": dict(Counter(e.action for e in events))},
            "teams": {"cuentas_creadas": len(graph_creates),
                      "cuentas_reutilizadas_preexistentes": reused_accounts,
                      "licencias_a1_asignadas": len(license_adds),
                      "licencias_a1_retiradas": len(license_removals),
                      "cuentas_deshabilitadas": len(disable_patches),
                      "patch_contrasena_temporal": len(password_patches)},
            "moodle": {"usuarios": len(moodle_transport.users),
                       "matriculas": len(moodle_transport.enrol_calls),
                       "desmatriculaciones": len(moodle_transport.unenrol_calls),
                       "matriculas_vigentes": len(moodle_transport.enrollments)},
            "smtp": {"intentos": len(smtp_outbox.attempts),
                     "entregados": len(smtp_outbox.delivered),
                     "fallidos_reintentados": len(smtp_outbox.attempts) - len(smtp_outbox.delivered),
                     "omitidos_cuenta_preexistente": skipped_mail},
            "outbox": {"succeeded": 130, "huerfanos": 0,
                       "pasadas": [provision_passes, suspend_passes, terminate_passes]},
        }

        def sanitized(record):
            clone = copy.deepcopy(record)
            headers = clone.get("headers") or {}
            if "Authorization" in headers:
                headers["Authorization"] = "Bearer [REDACTADO]"
            body = clone.get("body")
            if isinstance(body, dict):
                if "passwordProfile" in body:
                    body["passwordProfile"]["password"] = "[REDACTADO]"
                if "wstoken" in body:
                    body["wstoken"] = "[REDACTADO]"
            return clone

        graph_example = sanitized(next(
            r for r in graph_creates if "Núñez" in r["body"]["displayName"]))
        moodle_example = sanitized(next(
            r for r in capture if r["service"] == "moodle_rest"
            and r["body"]["wsfunction"] == "enrol_manual_enrol_users"))
        smtp_example = copy.deepcopy(smtp_outbox.delivered[0])
        smtp_example["text_body"] = re.sub(
            r"(Contraseña temporal: )\S+", r"\1[REDACTADO]", smtp_example["text_body"])

        print("\n===== CONTABILIDAD CANARIOS =====")
        print(json.dumps(accounting, indent=2, ensure_ascii=False))
        print("\n===== PAYLOAD GRAPH (POST /users, sanitizado) =====")
        print(json.dumps(graph_example, indent=2, ensure_ascii=False))
        print("\n===== PAYLOAD MOODLE REST (sanitizado) =====")
        print(json.dumps(moodle_example, indent=2, ensure_ascii=False))
        print("\n===== MENSAJE SMTP (sanitizado) =====")
        print(json.dumps(smtp_example, indent=2, ensure_ascii=False))
