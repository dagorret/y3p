"""URL routes for the three operational dashboards."""

from django.urls import path

from .views import (
    AdministracionDashboardView,
    ExtensionDashboardView,
    PostgradoDashboardView,
)

app_name = "portal"

urlpatterns = [
    path("administracion/", AdministracionDashboardView.as_view(), name="administracion"),
    path("postgrado/", PostgradoDashboardView.as_view(), name="postgrado"),
    path("extension/", ExtensionDashboardView.as_view(), name="extension"),
]
