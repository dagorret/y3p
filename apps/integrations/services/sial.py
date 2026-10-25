"""Cliente HTTP Basic para padrones y datos filiatorios UTI / SIAL."""

import re
from urllib.parse import urljoin, urlparse

from apps.integrations.dataclasses import SialStudentDTO
from apps.integrations.exceptions import (
    ConfigurationError,
    IntegrationError,
    ResourceNotFoundError,
)

from .base import BaseIntegrationClient


class SialClient(BaseIntegrationClient):
    """Cliente SIAL de solo lectura con autenticación Basic sobre HTTPS.

    Los paths se inyectan porque las rutas concretas dependen de la instalación
institucional. Por defecto consulta ``/preinscriptos``, ``/aspirantes``,
``/ingresantes`` y ``/students/{dni}``. Las respuestas se normalizan a DTOs y
los DNI solo se aceptan como cadenas de dígitos ASCII, evitando el sufijo ``.0``
que aparece al convertir padrones de planillas.
    """

    service_name = "sial"
    DEFAULT_ENDPOINTS = {
        "preinscriptos": "/preinscriptos",
        "aspirantes": "/aspirantes",
        "ingresantes": "/ingresantes",
        "details": "/students/{dni}",
    }
    CONDITION_BY_ENDPOINT = {
        "preinscriptos": "PREINSCRIPTO",
        "aspirantes": "ASPIRANTE",
        "ingresantes": "INGRESANTE",
    }

    def __init__(
        self,
        *,
        base_url=None,
        username=None,
        password=None,
        dry_run=False,
        timeout=8.0,
        logger=None,
        session=None,
        endpoints=None,
    ):
        """Guarda endpoint y credenciales sin contactar SIAL durante el inicio."""

        super().__init__(dry_run=dry_run, timeout=timeout, logger=logger, session=session)
        self.base_url = base_url.rstrip("/") if base_url else None
        self.username = username
        self.password = password
        self.endpoints = {**self.DEFAULT_ENDPOINTS, **(endpoints or {})}

    def _validate_configuration(self):
        """Exige endpoint HTTPS y credenciales Basic antes de lectura remota."""

        if not self.base_url or not self.username or not self.password:
            #FSIAL01-3001: Configuración Basic incompleta no inicia tráfico.
            raise ConfigurationError("SIAL requiere base_url, username y password.")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            #FSIAL02-3002: Credenciales Basic solo se transmiten por HTTPS.
            raise ConfigurationError("La URL de SIAL debe usar HTTPS.")

    def get_student_lists(self, campaign_id: str) -> dict[str, list[SialStudentDTO]]:
        """Consulta en orden preinscriptos, aspirantes e ingresantes.

        Cada llamada usa Basic Auth y timeout estricto. Una respuesta inválida
        aborta el método con error tipado; como son solo lecturas, no hay mutación
        externa ni transacción local que revertir.
        """

        if not campaign_id:
            #FSIAL03-3003: Campaña vacía no identifica un padrón consultable.
            raise ValueError("campaign_id no puede estar vacío.")
        result = {}
        #FSIAL04-3004: Las listas se consultan sucesivamente para preservar el contrato.
        for list_name in ("preinscriptos", "aspirantes", "ingresantes"):
            endpoint = self._endpoint(self.endpoints[list_name])
            with self.safe_request():
                #FSIAL05-3005: GET Basic protegido por TLS y timeout configurado.
                response = self.session.get(
                    endpoint,
                    params={"campaign_id": campaign_id},
                    auth=(self.username, self.password),
                    timeout=self.timeout,
                    headers={"Accept": "application/json"},
                )
            self._raise_for_status(response, resource=list_name)
            payload = self._decode_json(response)
            rows = self._extract_rows(payload, list_name)
            result[list_name] = [
                self._student_dto(row, self.CONDITION_BY_ENDPOINT[list_name])
                for row in rows
            ]
        return result

    def get_student_details(self, dni: str) -> SialStudentDTO | None:
        """Consulta datos filiatorios por DNI, retornando ``None`` ante HTTP 404."""

        self._validate_dni(dni)
        self._validate_configuration()
        path = self.endpoints["details"].format(dni=dni)
        with self.safe_request():
            #FSIAL06-3006: Consulta individual mantiene Basic Auth bajo HTTPS.
            response = self.session.get(
                self._endpoint(path),
                auth=(self.username, self.password),
                timeout=self.timeout,
                headers={"Accept": "application/json"},
            )
        try:
            self._raise_for_status(response, resource=f"dni={dni}")
        except ResourceNotFoundError:
            #FSIAL07-3007: Un 404 explícito significa que el padrón no contiene el DNI.
            return None
        payload = self._decode_json(response)
        if payload is None:
            #FSIAL08-3008: Una respuesta JSON null representa ausencia del registro.
            return None
        if isinstance(payload, dict) and "student" in payload:
            payload = payload["student"]
        if not isinstance(payload, dict):
            #FSIAL15-3015: Datos filiatorios requieren un objeto JSON.
            raise IntegrationError("SIAL devolvió el detalle en formato inválido.")
        return self._student_dto(payload, str(payload.get("condition", "")))

    def _endpoint(self, path):
        """Une base URL y path sin permitir que un path externo cambie el host."""

        if not path:
            #FSIAL09-3009: Endpoint vacío indica configuración incompleta.
            raise ConfigurationError("Falta configurar el path del endpoint SIAL.")
        parsed = urlparse(path)
        if parsed.scheme or parsed.netloc:
            #FSIAL16-3016: Un endpoint absoluto no puede redirigir Basic Auth a otro host.
            raise ConfigurationError("Los paths de SIAL deben ser relativos al host configurado.")
        return urljoin(f"{self.base_url}/", path.lstrip("/"))

    def _decode_json(self, response):
        """Decodifica JSON y convierte errores de formato a IntegrationError."""

        try:
            return response.json()
        except ValueError as error:
            #FSIAL10-3010: Los padrones deben estar serializados como JSON.
            raise IntegrationError("SIAL devolvió una respuesta JSON inválida.") from error

    def _extract_rows(self, payload, list_name):
        """Extrae una lista de estudiantes desde formatos JSON admitidos."""

        if isinstance(payload, dict):
            payload = payload.get("students", payload.get("data"))
        if not isinstance(payload, list):
            #FSIAL11-3011: La lista debe ser explícita; no convierte objetos arbitrarios.
            raise IntegrationError(f"SIAL devolvió {list_name} en formato inválido.")
        return payload

    def _student_dto(self, row, condition):
        """Valida DNI y proyecta campos requeridos al DTO inmutable."""

        if not isinstance(row, dict):
            #FSIAL12-3012: Cada fila del padrón debe ser un objeto JSON.
            raise IntegrationError("SIAL devolvió una fila de estudiante inválida.")
        dni = str(row.get("dni", ""))
        self._validate_dni(dni)
        required = (
            "first_name",
            "last_name",
            "personal_email",
            "career_code",
            "modality",
        )
        missing = [name for name in required if row.get(name) is None]
        if missing:
            #FSIAL13-3013: Un DTO incompleto no se entrega como padrón válido.
            raise IntegrationError(f"Fila SIAL incompleta; faltan: {', '.join(missing)}.")
        return SialStudentDTO(
            dni=dni,
            first_name=str(row["first_name"]),
            last_name=str(row["last_name"]),
            personal_email=str(row["personal_email"]),
            career_code=str(row["career_code"]),
            modality=str(row["modality"]),
            condition=str(row.get("condition") or condition),
        )

    def _validate_dni(self, dni):
        """Acepta solo DNI no vacío compuesto por dígitos ASCII."""

        if not isinstance(dni, str) or re.fullmatch(r"[0-9]+", dni) is None:
            #FSIAL14-3014: Rechaza DNI con decimales, signos, espacios o texto.
            raise IntegrationError("El DNI de SIAL debe ser una cadena numérica sin sufijo .0.")
