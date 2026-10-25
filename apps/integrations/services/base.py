"""Primitivas compartidas de HTTP, logging seguro y traducción de errores.

``BaseIntegrationClient`` no conoce Django ni el esquema local. Cada subcliente
le entrega un transporte tipo ``requests``; el contexto ``safe_request`` traduce
fallos de red y ``_raise_for_status`` asigna errores tipados por estado HTTP.
"""

import logging
from contextlib import contextmanager
from typing import Any

import requests

from apps.integrations.exceptions import (
    AuthenticationError,
    IntegrationError,
    OperationForbiddenError,
    RateLimitExceededError,
    RemoteServiceUnavailableError,
    ResourceAlreadyExistsError,
    ResourceNotFoundError,
)


class BaseIntegrationClient:
    """Base sin ORM para clientes HTTP externos.

    Args:
        dry_run: Intercepta operaciones de escritura antes de cualquier red.
        timeout: Timeout estricto aplicado a cada llamada HTTP.
        logger: Logger inyectable para pruebas; por defecto se crea por cliente.
        session: Transporte compatible con ``requests``; se usa el módulo
            ``requests`` por defecto para que las llamadas sean fácilmente
            interceptables por dobles de prueba.

    No abre conexiones durante la construcción. Los errores de configuración se
    validan al entrar a la operación que los necesita.
    """

    service_name = "integration"
    _redacted_log_keys = (
        "password",
        "secret",
        "token",
        "authorization",
        "email",
        "upn",
        "username",
        "dni",
        "recipient",
        "first_name",
        "last_name",
        "display_name",
        "personal_email",
    )

    def __init__(self, *, dry_run=False, timeout=10.0, logger=None, session=None):
        """Guarda dependencias inyectables sin iniciar actividad de red."""

        #FINTBASE01-0001: Timeout positivo es precondición de toda llamada remota.
        if timeout <= 0:
            #FINTBASE01-0001: Rechaza timeout nulo o negativo antes de cualquier I/O.
            raise ValueError("timeout debe ser mayor que cero.")
        self.dry_run = bool(dry_run)
        self.timeout = float(timeout)
        self.logger = logger or logging.getLogger(f"apps.integrations.{self.service_name}")
        self.session = session if session is not None else requests

    @contextmanager
    def safe_request(self):
        """Traduce excepciones de transporte a errores de dominio estables.

        El contexto no reintenta ni suprime fallos: timeout y conexión caída
        pasan a ``RemoteServiceUnavailableError``; otros errores de requests
        pasan a ``IntegrationError``. La transacción local no se toca.
        """

        try:
            #FINTBASE02-0002: Toda llamada de red usa este perímetro de traducción.
            yield
        except requests.Timeout as error:
            #FINTBASE03-0003: Timeout remoto se expone como indisponibilidad tipada.
            raise RemoteServiceUnavailableError(
                f"Timeout al contactar {self.service_name}."
            ) from error
        except requests.ConnectionError as error:
            #FINTBASE04-0004: No se filtra RequestException cruda al dominio.
            raise RemoteServiceUnavailableError(
                f"No se pudo establecer conexión con {self.service_name}."
            ) from error
        except requests.RequestException as error:
            #FINTBASE05-0005: Fallos restantes de transporte quedan tipados.
            raise IntegrationError(
                f"Error HTTP de transporte en {self.service_name}: {error.__class__.__name__}."
            ) from error

    def _intercept_dry_run(self, operation_name: str, **kwargs) -> bool:
        """Registra de forma estructurada una escritura simulada y devuelve si interceptó.

        Los valores con credenciales o datos personales se redactan antes del
        log. Si retorna ``True``, el llamador debe construir el DTO simulado y
        salir sin obtener token ni invocar el transporte.
        """

        if not self.dry_run:
            #FINTBASE06-0006: En modo real la operación puede continuar hacia HTTP.
            return False

        #FINTBASE07-0007: El bypass sucede antes de token, socket o SMTP.
        safe_payload = self._safe_log_payload(kwargs)
        self.logger.warning(
            "integration_operation_simulated",
            extra={
                "integration": self.service_name,
                "operation": operation_name,
                "dry_run": True,
                "payload": safe_payload,
            },
        )
        return True

    def _safe_log_payload(self, values: dict[str, Any]) -> dict[str, Any]:
        """Redacta campos secretos y personales de la estructura del log."""

        safe = {}
        for key, value in values.items():
            lowered = key.casefold()
            if any(secret in lowered for secret in self._redacted_log_keys):
                #FINTBASE08-0008: No se escriben secretos ni identificadores personales en logs.
                safe[key] = "[REDACTED]"
            elif isinstance(value, bytes):
                safe[key] = {"byte_length": len(value)}
            elif isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value
            else:
                #FINTBASE09-0009: Evita serializar objetos arbitrarios potencialmente sensibles.
                safe[key] = f"<{value.__class__.__name__}>"
        return safe

    def _raise_for_status(self, response, *, resource=None):
        """Traduce un estado HTTP no exitoso a una excepción específica.

        Args:
            response: Objeto response compatible con ``requests.Response``.
            resource: Identificador lógico para mensajes y conflictos.

        Returns:
            ``None`` para estados HTTP 2xx.

        Raises:
            AuthenticationError: Para 401.
            OperationForbiddenError: Para 403.
            ResourceNotFoundError: Para 404.
            ResourceAlreadyExistsError: Para 409.
            RateLimitExceededError: Para 429, con ``retry_after`` cuando existe.
            RemoteServiceUnavailableError: Para 5xx.
            IntegrationError: Para otros estados 4xx.
        """

        status = response.status_code
        if 200 <= status < 300:
            #FINTBASE10-0010: Los estados 2xx permiten parsear el cuerpo.
            return None
        if status == 401:
            #FINTBASE11-0011: 401 representa credencial o token rechazado.
            raise AuthenticationError(f"{self.service_name} rechazó la autenticación.")
        if status == 403:
            #FINTBASE12-0012: 403 representa permisos insuficientes.
            raise OperationForbiddenError(f"{self.service_name} denegó la operación.")
        if status == 404:
            #FINTBASE13-0013: 404 se conserva como ausencia del recurso solicitado.
            raise ResourceNotFoundError(
                f"Recurso no encontrado en {self.service_name}: {resource or 'desconocido'}."
            )
        if status == 409:
            #FINTBASE14-0014: 409 se modela como conflicto, nunca como éxito por texto.
            raise ResourceAlreadyExistsError(
                f"Conflicto de recurso en {self.service_name}.",
                resource=resource,
                details=self._response_details(response),
            )
        if status == 429:
            #FINTBASE15-0015: Lee Retry-After sin inferirlo del mensaje remoto.
            retry_after = self._retry_after(response)
            raise RateLimitExceededError(
                f"Límite de solicitudes excedido en {self.service_name}.",
                retry_after=retry_after,
            )
        if status >= 500:
            #FINTBASE16-0016: Los estados 5xx indican indisponibilidad remota.
            raise RemoteServiceUnavailableError(
                f"{self.service_name} respondió HTTP {status}."
            )
        if status >= 400:
            #FINTBASE17-0017: Otros 4xx son errores de integración, no éxito.
            raise IntegrationError(f"{self.service_name} respondió HTTP {status}.")
        #FINTBASE18-0018: Respuestas fuera del rango conocido no se aceptan como éxito.
        raise IntegrationError(f"Estado HTTP inesperado de {self.service_name}: {status}.")

    def _response_details(self, response):
        """Extrae datos JSON estructurados si el cuerpo permite decodificación."""

        try:
            body = response.json()
        except (ValueError, AttributeError):
            #FINTBASE19-0019: El cuerpo no JSON no altera el tipo del error HTTP.
            return {}
        return body if isinstance(body, dict) else {"body_type": type(body).__name__}

    def _retry_after(self, response):
        """Convierte ``Retry-After`` numérico en segundos, o retorna ``None``."""

        value = getattr(response, "headers", {}).get("Retry-After")
        if value is None:
            #FINTBASE20-0020: El proveedor puede omitir la sugerencia de espera.
            return None
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            #FINTBASE21-0021: No interpreta formatos HTTP-date como segundos.
            return None
