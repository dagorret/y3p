"""Errores tipados para aislar fallos de proveedores externos.

Los consumidores pueden decidir reintentar, corregir configuración o informar
un conflicto sin depender de los textos remotos ni de subcadenas ambiguas.
"""


class IntegrationError(Exception):
    """Clase base de fallos de integración identificables por tipo."""


class ConfigurationError(IntegrationError):
    """Indica que falta o es inválida una URL, credencial o parámetro local."""


class AuthenticationError(IntegrationError):
    """Indica que el proveedor rechazó una credencial o un token."""


class ResourceNotFoundError(IntegrationError):
    """Indica que el usuario, curso, SKU u otro recurso solicitado no existe."""


class ResourceAlreadyExistsError(IntegrationError):
    """Indica un conflicto estructurado al crear un recurso ya existente.

    ``resource`` conserva el identificador legible cuando está disponible y
    ``details`` conserva datos estructurados seguros para diagnóstico.
    """

    def __init__(self, message, *, resource=None, details=None):
        """Inicializa el conflicto con mensaje humano y metadatos opcionales."""

        super().__init__(message)
        self.resource = resource
        self.details = details or {}


class RemoteServiceUnavailableError(IntegrationError):
    """Indica timeout, fallo de conexión o respuesta 5xx remota."""


class RateLimitExceededError(IntegrationError):
    """Indica respuesta HTTP 429 y expone el tiempo sugerido de espera."""

    def __init__(self, message, *, retry_after=None):
        """Inicializa el error y conserva ``Retry-After`` como segundos."""

        super().__init__(message)
        self.retry_after = retry_after


class OperationForbiddenError(IntegrationError):
    """Indica que el token o usuario no posee permisos para la operación."""
