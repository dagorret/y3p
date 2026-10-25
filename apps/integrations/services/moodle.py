"""Cliente REST para funciones Moodle Web Services de la FCE."""

import threading
import time
from urllib.parse import urlparse

from apps.integrations.dataclasses import (
    MoodleEnrollmentResult,
    MoodleGroupResult,
    MoodleUserResult,
)
from apps.integrations.exceptions import (
    AuthenticationError,
    ConfigurationError,
    IntegrationError,
    OperationForbiddenError,
    ResourceAlreadyExistsError,
    ResourceNotFoundError,
)

from .base import BaseIntegrationClient


class MoodleRestClient(BaseIntegrationClient):
    """Cliente sin ORM para funciones Moodle REST.

    Todas las operaciones se envían como POST con ``wstoken`` y formato JSON,
    según el protocolo Moodle. Las llamadas se espacian globalmente por proceso
    con un lock y un intervalo mínimo de 0.4 segundos entre inicios. Los métodos
    de escritura se interceptan antes de cualquier POST cuando ``dry_run``.
    """

    service_name = "moodle"
    minimum_request_interval = 0.4
    _rate_limit_lock = threading.Lock()
    _last_request_started = None

    def __init__(
        self,
        *,
        base_url=None,
        token=None,
        dry_run=False,
        timeout=10.0,
        logger=None,
        session=None,
        minimum_request_interval=0.4,
    ):
        """Guarda URL, token y límites sin realizar una llamada de prueba."""

        super().__init__(dry_run=dry_run, timeout=timeout, logger=logger, session=session)
        #FMOODLE01-2001: El intervalo configurado no puede violar la política mínima FCE.
        if minimum_request_interval < 0.4:
            #FMOODLE01-2001: Rechaza spacing menor a 0.4 segundos antes de consultas.
            raise ValueError("minimum_request_interval debe ser al menos 0.4 segundos.")
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        self.minimum_request_interval = float(minimum_request_interval)

    def _validate_configuration(self):
        """Exige token y URL HTTPS antes de enviar parámetros de web service."""

        if not self.base_url or not self.token:
            #FMOODLE02-2002: El endpoint y wstoken son obligatorios en modo real.
            raise ConfigurationError("Moodle requiere base_url y token.")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            #FMOODLE03-2003: No transmite wstoken fuera de HTTPS.
            raise ConfigurationError("La URL de Moodle debe usar HTTPS.")

    def _pace_request(self):
        """Espera dentro de un lock para separar los inicios de POST al menos 0.4s."""

        cls = type(self)
        with cls._rate_limit_lock:
            now = time.monotonic()
            last_started = cls._last_request_started
            if last_started is not None:
                wait_seconds = self.minimum_request_interval - (now - last_started)
                if wait_seconds > 0:
                    #FMOODLE04-2004: Serializa solicitudes para respetar el rate limit institucional.
                    time.sleep(wait_seconds)
            #FMOODLE05-2005: Reserva el próximo slot antes de soltar el lock.
            cls._last_request_started = time.monotonic()

    def _call(self, function, *, data=None, resource=None):
        """Llama a una función core y traduce errores Moodle estructurados."""

        self._validate_configuration()
        self._pace_request()
        payload = {
            "wstoken": self.token,
            "moodlewsrestformat": "json",
            "wsfunction": function,
        }
        payload.update(data or {})
        with self.safe_request():
            #FMOODLE06-2006: Toda función Moodle viaja por POST con timeout estricto.
            response = self.session.post(
                f"{self.base_url}/webservice/rest/server.php",
                data=payload,
                timeout=self.timeout,
            )
        self._raise_for_status(response, resource=resource or function)
        try:
            body = response.json()
        except ValueError as error:
            #FMOODLE07-2007: JSON inválido no se confunde con una respuesta vacía exitosa.
            raise IntegrationError("Moodle devolvió una respuesta JSON inválida.") from error
        self._raise_moodle_error(body, resource=resource or function)
        return body

    def _raise_moodle_error(self, body, *, resource):
        """Interpreta códigos de error exactos del JSON de Moodle.

        Nunca compara el texto ``message`` con fragmentos de éxito. Los códigos
        conocidos producen errores de dominio tipados; cualquier otro error
        estructurado queda como ``IntegrationError``.
        """

        if not isinstance(body, dict) or not body.get("exception"):
            #FMOODLE08-2008: La forma ordinaria de Moodle no contiene clave exception.
            return
        code = body.get("errorcode")
        details = {"errorcode": code, "exception": body.get("exception")}
        if code in {"invalidtoken", "invalidlogin"}:
            #FMOODLE09-2009: Errorcode de autenticación exacto, sin analizar message.
            raise AuthenticationError(f"Moodle rechazó la autenticación ({code}).")
        if code in {"nopermissions", "accessexception"}:
            #FMOODLE10-2010: Moodle denegó permisos explícitamente.
            raise OperationForbiddenError(f"Moodle denegó la operación ({code}).")
        if code in {"invalidcourseid", "invaliduser", "invalidgroupid"}:
            #FMOODLE11-2011: Errorcode inequívoco de recurso inexistente.
            raise ResourceNotFoundError(f"Recurso inexistente en Moodle ({code}): {resource}.")
        if code in {
            "usernameexists",
            "useralreadyenrolled",
            "enrolmentalreadyexists",
            "groupalreadyexists",
            "groupmemberexists",
        }:
            #FMOODLE12-2012: Conflicto estructurado conserva su errorcode para idempotencia.
            raise ResourceAlreadyExistsError(
                f"Conflicto de recurso en Moodle ({code}).",
                resource=resource,
                details=details,
            )
        #FMOODLE13-2013: Error estructurado desconocido se propaga como fallo, nunca como éxito.
        raise IntegrationError(f"Moodle devolvió un error estructurado ({code or 'sin código'}).")

    def check_connection(self) -> dict:
        """Consulta información del sitio con ``core_webservice_get_site_info``."""

        body = self._call("core_webservice_get_site_info")
        if not isinstance(body, dict):
            #FMOODLE14-2014: Site info requiere un objeto JSON.
            raise IntegrationError("Moodle devolvió site info con formato inválido.")
        return body

    def get_user_by_username(self, username: str) -> MoodleUserResult | None:
        """Busca un usuario exacto por username y retorna ``None`` ante lista vacía."""

        if not username:
            #FMOODLE15-2015: Username vacío no es una consulta válida.
            raise ValueError("username no puede estar vacío.")
        body = self._call(
            "core_user_get_users_by_field",
            data={"field": "username", "values[0]": username},
            resource=username,
        )
        if not isinstance(body, list):
            #FMOODLE16-2016: Moodle documenta una lista para búsqueda por campo.
            raise IntegrationError("Moodle devolvió usuarios en formato inválido.")
        if not body:
            #FMOODLE17-2017: Lista vacía representa ausencia normal del usuario.
            return None
        user = body[0]
        return self._user_result(user, created=False)

    def create_user(
        self,
        username: str,
        first_name: str,
        last_name: str,
        email: str,
        auth: str = "oidc",
    ) -> MoodleUserResult:
        """Obtiene o crea un usuario Moodle; 409/usernameexists conserva conflicto tipado.

        Antes de un alta real consulta el username para hacer la operación
        idempotente. En dry-run no ejecuta ni consulta la API y retorna un ID
        sintético negativo que no puede confundirse con uno persistido.
        """

        if not all((username, first_name, last_name, email, auth)):
            #FMOODLE18-2018: Los datos requeridos se validan antes de solicitar Moodle.
            raise ValueError("username, nombres, email y auth son obligatorios.")
        if self._intercept_dry_run(
            "create_user",
            username=username,
            first_name=first_name,
            last_name=last_name,
            email=email,
            auth=auth,
        ):
            #FMOODLE19-2019: Bypass antecede incluso a la búsqueda idempotente.
            return MoodleUserResult(-1, username, email, created=True, simulated=True)

        existing = self.get_user_by_username(username)
        if existing is not None:
            #FMOODLE20-2020: Recurso ya presente satisface el estado deseado sin duplicarlo.
            return MoodleUserResult(
                existing.id, existing.username, existing.email, created=False
            )
        body = self._call(
            "core_user_create_users",
            data={
                "users[0][username]": username,
                "users[0][firstname]": first_name,
                "users[0][lastname]": last_name,
                "users[0][email]": email,
                "users[0][auth]": auth,
            },
            resource=username,
        )
        if not isinstance(body, list) or not body:
            #FMOODLE21-2021: Alta sin registro devuelto no cuenta como confirmada.
            raise IntegrationError("Moodle no devolvió el usuario creado.")
        return self._user_result(body[0], created=True)

    def get_course_by_shortname(self, shortname: str) -> dict | None:
        """Busca curso por shortname con ``core_course_get_courses_by_field``."""

        if not shortname:
            #FMOODLE22-2022: shortname vacío no identifica un curso.
            raise ValueError("shortname no puede estar vacío.")
        body = self._call(
            "core_course_get_courses_by_field",
            data={"field": "shortname", "value": shortname},
            resource=shortname,
        )
        if not isinstance(body, dict):
            #FMOODLE23-2023: Moodle retorna objeto con colección courses.
            raise IntegrationError("Moodle devolvió cursos en formato inválido.")
        courses = body.get("courses", [])
        if not isinstance(courses, list):
            #FMOODLE24-2024: Rechaza un miembro courses mal formado.
            raise IntegrationError("La colección courses de Moodle no es una lista.")
        return next(
            (course for course in courses if course.get("shortname") == shortname), None
        )

    def enrol_user(self, user_id: int, course_id: int, role_id: int = 5) -> MoodleEnrollmentResult:
        """Matricula un usuario y trata ``useralreadyenrolled`` como estado idempotente."""

        self._validate_enrollment_ids(user_id, course_id, role_id)
        if self._intercept_dry_run(
            "enrol_user", user_id=user_id, course_id=course_id, role_id=role_id
        ):
            #FMOODLE39-2039: Bypass de matrícula evita POST real.
            return MoodleEnrollmentResult(user_id, course_id, role_id, True, simulated=True)
        try:
            self._call(
                "enrol_manual_enrol_users",
                data={
                    "enrolments[0][roleid]": role_id,
                    "enrolments[0][userid]": user_id,
                    "enrolments[0][courseid]": course_id,
                },
                resource=f"user={user_id},course={course_id}",
            )
        except ResourceAlreadyExistsError as error:
            #FMOODLE25-2025: Código Moodle exacto ya matriculado equivale a estado deseado.
            if error.details.get("errorcode") in {
                "useralreadyenrolled",
                "enrolmentalreadyexists",
            }:
                return MoodleEnrollmentResult(user_id, course_id, role_id, True)
            raise
        return MoodleEnrollmentResult(user_id, course_id, role_id, True)

    def unenrol_user(
        self, user_id: int, course_id: int, role_id: int = 5
    ) -> MoodleEnrollmentResult:
        """Quita la matrícula del usuario mediante ``enrol_manual_unenrol_users``."""

        self._validate_enrollment_ids(user_id, course_id, role_id)
        if self._intercept_dry_run(
            "unenrol_user", user_id=user_id, course_id=course_id, role_id=role_id
        ):
            #FMOODLE26-2026: Bypass de baja de matrícula evita POST real.
            return MoodleEnrollmentResult(user_id, course_id, role_id, True, simulated=True)
        self._call(
            "enrol_manual_unenrol_users",
            data={
                "enrolments[0][userid]": user_id,
                "enrolments[0][courseid]": course_id,
                "enrolments[0][roleid]": role_id,
            },
            resource=f"user={user_id},course={course_id}",
        )
        return MoodleEnrollmentResult(user_id, course_id, role_id, True)

    def get_or_create_group(self, course_id: int, group_name: str) -> int:
        """Retorna grupo existente o lo crea; en dry-run usa ID sintético ``-1``.

        Dry-run trata la operación compuesta completa como mutación potencial y
        retorna un ID sintético sin consultar ni crear grupos remotamente.
        """

        if not group_name or course_id == 0 or course_id < -1 or (course_id < 1 and not self.dry_run):
            #FMOODLE27-2027: Curso y nombre son requeridos para un grupo válido.
            raise ValueError("course_id positivo y group_name son obligatorios.")
        if self._intercept_dry_run(
            "get_or_create_group", course_id=course_id, group_name=group_name
        ):
            #FMOODLE38-2038: La operación compuesta se simula antes de cualquier POST.
            #FMOODLE30-2030: El ID negativo identifica un grupo sintético no persistido.
            return -1
        body = self._call(
            "core_group_get_course_groups",
            data={"courseid": course_id},
            resource=f"course={course_id}",
        )
        if not isinstance(body, list):
            #FMOODLE28-2028: La búsqueda de grupos debe retornar una lista.
            raise IntegrationError("Moodle devolvió grupos en formato inválido.")
        for group in body:
            if isinstance(group, dict) and group.get("name") == group_name:
                #FMOODLE29-2029: Reutiliza grupo encontrado sin una segunda escritura.
                return int(group["id"])

        created = self._call(
            "core_group_create_groups",
            data={
                "groups[0][courseid]": course_id,
                "groups[0][name]": group_name,
            },
            resource=group_name,
        )
        if not isinstance(created, list) or not created:
            #FMOODLE31-2031: Sin ID devuelto no se confirma grupo creado.
            raise IntegrationError("Moodle no devolvió el grupo creado.")
        return int(created[0]["id"])

    def add_user_to_group(
        self, group_id: int, user_id: int, *, course_id: int = 0
    ) -> MoodleGroupResult:
        """Agrega miembro con ``core_group_add_group_members``.

        ``course_id`` se incluye en el DTO si el llamador ya lo conoce; cero
        indica que no se recibió contexto del curso y evita una consulta adicional.
        """

        self._validate_group_ids(group_id, user_id)
        if self._intercept_dry_run(
            "add_user_to_group", group_id=group_id, user_id=user_id, course_id=course_id
        ):
            #FMOODLE32-2032: Bypass evita la función de escritura de miembros.
            return MoodleGroupResult(group_id, course_id, user_id, "add", True, True)
        self._call(
            "core_group_add_group_members",
            data={"members[0][groupid]": group_id, "members[0][userid]": user_id},
            resource=f"group={group_id},user={user_id}",
        )
        return MoodleGroupResult(group_id, course_id, user_id, "add", True)

    def remove_user_from_group(
        self, group_id: int, user_id: int, *, course_id: int = 0
    ) -> MoodleGroupResult:
        """Quita miembro con ``core_group_delete_group_members``."""

        self._validate_group_ids(group_id, user_id)
        if self._intercept_dry_run(
            "remove_user_from_group", group_id=group_id, user_id=user_id, course_id=course_id
        ):
            #FMOODLE33-2033: Bypass evita la función remota de baja de miembro.
            return MoodleGroupResult(group_id, course_id, user_id, "remove", True, True)
        self._call(
            "core_group_delete_group_members",
            data={"members[0][groupid]": group_id, "members[0][userid]": user_id},
            resource=f"group={group_id},user={user_id}",
        )
        return MoodleGroupResult(group_id, course_id, user_id, "remove", True)

    def _validate_enrollment_ids(self, user_id, course_id, role_id):
        """Verifica IDs positivos para matrícula manual."""

        valid_user = user_id > 0 or (self.dry_run and user_id == -1)
        valid_course = course_id > 0 or (self.dry_run and course_id == -1)
        if not valid_user or not valid_course or role_id <= 0:
            #FMOODLE34-2034: IDs negativos solo se aceptan como sentinelas en dry-run.
            raise ValueError("user_id, course_id y role_id deben ser positivos.")

    def _validate_group_ids(self, group_id, user_id):
        """Verifica que IDs de grupo y usuario sean positivos."""

        valid_group = group_id > 0 or (self.dry_run and group_id == -1)
        valid_user = user_id > 0 or (self.dry_run and user_id == -1)
        if not valid_group or not valid_user:
            #FMOODLE35-2035: IDs negativos solo se aceptan para simulación encadenada.
            raise ValueError("group_id y user_id deben ser positivos.")

    def _user_result(self, user, *, created):
        """Convierte respuesta Moodle de usuario a DTO estricto."""

        if not isinstance(user, dict) or "id" not in user:
            #FMOODLE36-2036: La respuesta debe incluir ID numérico persistido.
            raise IntegrationError("Moodle devolvió un usuario incompleto.")
        try:
            user_id = int(user["id"])
        except (TypeError, ValueError) as error:
            #FMOODLE37-2037: El ID Moodle es entero, no se acepta coerción inválida.
            raise IntegrationError("Moodle devolvió un ID de usuario inválido.") from error
        return MoodleUserResult(
            id=user_id,
            username=str(user.get("username", "")),
            email=str(user.get("email", "")),
            created=created,
        )
