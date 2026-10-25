"""Application configuration for the public HTMX portal."""

from django.apps import AppConfig


class PortalConfig(AppConfig):
    """Register portal dashboards and use big integer primary keys by default."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.portal"
    verbose_name = "Portal Operativo"
