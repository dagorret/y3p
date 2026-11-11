"""Errores tipados para aislar fallos de proveedores externos.

Los consumidores pueden decidir reintentar, corregir configuración o informar
un conflicto sin depender de los textos remotos ni de subcadenas ambiguas.
"""


#FINTEGRATIONSEXCEPTIONS001-8001: Declara la excepción de dominio IntegrationError para distinguir esta regla.
class IntegrationError(Exception):
    """Clase base de fallos de integración identificables por tipo."""


#FINTEGRATIONSEXCEPTIONS002-8002: Declara la excepción de dominio ConfigurationError para distinguir esta regla.
class ConfigurationError(IntegrationError):
    """Indica que falta o es inválida una URL, credencial o parámetro local."""


#FINTEGRATIONSEXCEPTIONS003-8003: Declara la excepción de dominio AuthenticationError para distinguir esta regla.
class AuthenticationError(IntegrationError):
    """Indica que el proveedor rechazó una credencial o un token."""


#FINTEGRATIONSEXCEPTIONS004-8004: Declara la excepción de dominio ResourceNotFoundError para distinguir esta regla.
class ResourceNotFoundError(IntegrationError):
    """Indica que el usuario, curso, SKU u otro recurso solicitado no existe."""


#FINTEGRATIONSEXCEPTIONS005-8005: Declara la excepción de dominio ResourceAlreadyExistsError para distinguir esta regla.
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


#FINTEGRATIONSEXCEPTIONS006-8006: Declara la excepción de dominio RemoteServiceUnavailableError para distinguir esta regla.
class RemoteServiceUnavailableError(IntegrationError):
    """Indica timeout, fallo de conexión o respuesta 5xx remota."""


#FINTEGRATIONSEXCEPTIONS007-8007: Declara la excepción de dominio RateLimitExceededError para distinguir esta regla.
class RateLimitExceededError(IntegrationError):
    """Indica respuesta HTTP 429 y expone el tiempo sugerido de espera."""

    def __init__(self, message, *, retry_after=None):
        """Inicializa el error y conserva ``Retry-After`` como segundos."""

        super().__init__(message)
        self.retry_after = retry_after


#FINTEGRATIONSEXCEPTIONS008-8008: Declara la excepción de dominio OperationForbiddenError para distinguir esta regla.
class OperationForbiddenError(IntegrationError):
    """Indica que el token o usuario no posee permisos para la operación."""
