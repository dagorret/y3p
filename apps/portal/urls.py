"""Rutas de los portales operativos y de la consola administrativa."""

from django.urls import path
from django.contrib.auth.views import LoginView

from .views import (
    AdministracionDashboardView,
    QueryBuilderView,
    AdministracionSearchView,
    ExtensionDashboardView,
    PostgradoDashboardView,
)
from .outbox_monitor import OutboxMonitorView, OutboxBadgeView
from .academic_views import ScopedIdentityView
from . import admin_views
from . import global_views
from .catalog_views import CatalogSyncView, CatalogChannelsView
from .snippet_views import SnippetCreateView, SnippetDeleteView
from .student_export import StudentExportView
from .bulk_views import StudentBulkActionView
from .team_views import TeamConsoleActionView, TeamMembersView

app_name = "portal"

urlpatterns = [
    path('administracion/outbox/', OutboxMonitorView.as_view(), name='outbox_monitor'),
    path('administracion/outbox/indicador/', OutboxBadgeView.as_view(), name='outbox_badge'),
    path('administracion/ingresar/', LoginView.as_view(
        template_name='portal/administracion/login.html', next_page='portal:administracion',
    ), name='administracion_login'),
    path("administracion/sincronizacion/", CatalogSyncView.as_view(), name="catalog_sync"),
    path("administracion/catalogos/canales/", CatalogChannelsView.as_view(), name="catalog_channels"),
    path("administracion/vistas/", QueryBuilderView.as_view(), name="query_builder"),
    path("administracion/", AdministracionDashboardView.as_view(), name="administracion"),
    path('administracion/snippets/guardar/', SnippetCreateView.as_view(), name='snippet_create'),
    path('administracion/snippets/<int:snippet_id>/eliminar/', SnippetDeleteView.as_view(), name='snippet_delete'),
    path('administracion/exportar/', StudentExportView.as_view(), name='student_export'),
    path('administracion/acciones-masivas/', StudentBulkActionView.as_view(), name='student_bulk_action'),
    path('administracion/personas/nueva/', global_views.GlobalCreationView.as_view(), name='person_create'),
    path('administracion/cuentas-funcionales/nueva/', global_views.FunctionalAccountCreationView.as_view(), name='functional_create'),
    path('administracion/teams/', global_views.TeamsView.as_view(), name='teams'),
    path('administracion/teams/nuevo/', global_views.TeamCreationView.as_view(), name='team_create'),
    path('administracion/teams/purgar/', global_views.TeamPurgeView.as_view(), name='team_purge_form'),
    path('administracion/teams/<uuid:team_id>/purgar/', global_views.TeamPurgeView.as_view(), name='team_purge'),
    path('administracion/teams/<uuid:team_id>/miembros/', TeamMembersView.as_view(), name='team_members'),
    path('administracion/teams/<uuid:team_id>/poblar/', TeamConsoleActionView.as_view(operation='populate'), name='team_populate'),
    path('administracion/teams/<uuid:team_id>/configurar/', TeamConsoleActionView.as_view(operation='configure'), name='team_configure'),
    path('administracion/teams/<uuid:team_id>/actualizar/', TeamConsoleActionView.as_view(operation='refresh'), name='team_refresh'),
    path('administracion/buscar/', AdministracionSearchView.as_view(), name='administracion_buscar'),
    path('administracion/personas/<int:identity_id>/', admin_views.Identity360View.as_view(), name='identity360_plural'),
    path('administracion/persona/<int:identity_id>/actualizar/', admin_views.IdentityPlatformRefreshView.as_view(), name='identity_platform_refresh'),
    path('administracion/persona/<int:identity_id>/', admin_views.Identity360View.as_view(), name='identity360'),
    path('administracion/persona/<int:identity_id>/contacto/', admin_views.IdentityContactView.as_view(), name='identity_contact'),
    path('administracion/persona/<int:identity_id>/metadata/', admin_views.IdentityMetadataView.as_view(), name='identity_metadata'),
    path('administracion/persona/<int:identity_id>/vinculacion/<int:affiliation_id>/transicion/', admin_views.AffiliationTransitionView.as_view(), name='affiliation_transition'),
    path('administracion/persona/<int:identity_id>/vinculacion/<int:affiliation_id>/ttl/', admin_views.AffiliationTTLView.as_view(), name='affiliation_ttl'),
    path('administracion/persona/<int:identity_id>/credenciales/', admin_views.IdentitySupportView.as_view(), {'action': 'credentials'}, name='identity_credentials'),
    path('administracion/persona/<int:identity_id>/sesiones/', admin_views.IdentitySupportView.as_view(), {'action': 'sessions'}, name='identity_sessions'),
    path('administracion/persona/<int:identity_id>/licencia/', admin_views.IdentitySupportView.as_view(), {'action': 'license'}, name='identity_license'),
    path('administracion/persona/<int:identity_id>/suspender/', admin_views.IdentitySupportView.as_view(), {'action': 'suspend'}, name='identity_suspend'),
    path('administracion/persona/<int:identity_id>/moodle/inscribir/', admin_views.MoodleExpressView.as_view(), {'action': 'moodle_enrol'}, name='identity_moodle_enrol'),
    path('administracion/persona/<int:identity_id>/moodle/desmatricular/', admin_views.MoodleExpressView.as_view(), {'action': 'moodle_unenrol'}, name='identity_moodle_unenrol'),
    path("postgrado/inscripcion/", PostgradoDashboardView.as_view(), name="postgrado_registration"),
    path("extension/inscripcion/", ExtensionDashboardView.as_view(), name="extension_registration"),
    path("postgrado/", AdministracionDashboardView.as_view(scope="POSGRADO"), name="postgrado"),
    path("extension/", AdministracionDashboardView.as_view(scope="EXTENSION"), name="extension"),
]

#FACADEMICROUTES01-10203: Cada ruta fija el ámbito en el servidor, sin confiar en parámetros ocultos.
for prefix, scope in (('postgrado', 'POSGRADO'), ('extension', 'EXTENSION')):
    urlpatterns += [
        path(f'{prefix}/exportar/', StudentExportView.as_view(scope=scope), name=f'{prefix}_export'),
        path(f'{prefix}/acciones-masivas/', StudentBulkActionView.as_view(scope=scope), name=f'{prefix}_bulk'),
        path(f'{prefix}/persona/<int:identity_id>/', ScopedIdentityView.as_view(scope=scope), name=f'{prefix}_detail'),
        path(f'{prefix}/vistas/', QueryBuilderView.as_view(scope=scope), name=f'{prefix}_builder'),
        path(f'{prefix}/snippets/guardar/', SnippetCreateView.as_view(scope=scope), name=f'{prefix}_snippet_create'),
        path(f'{prefix}/snippets/<int:snippet_id>/eliminar/', SnippetDeleteView.as_view(scope=scope), name=f'{prefix}_snippet_delete'),
    ]
