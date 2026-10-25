"""Django application configuration for asynchronous provisioning."""

from django.apps import AppConfig


class ProvisioningConfig(AppConfig):
    """Registers worker orchestration services without owning ORM tables."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.provisioning"
    verbose_name = "Aprovisionamiento"
