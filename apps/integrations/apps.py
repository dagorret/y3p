"""Configuración Django del paquete de clientes de integración."""

from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    """Registra clientes y configuración local sin iniciar conexiones externas."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations"
    verbose_name = "Clientes de Integración Externa"
