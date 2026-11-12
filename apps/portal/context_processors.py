"""Visibilidad derivada de las mismas capacidades que protegen el servidor."""

from django.urls import reverse

from apps.people.permissions import (
    ADMINISTRATE_STUDENT_CRITICAL, OPERATE_STUDENT_DAILY, VIEW_STUDENT_360, has_portal_permission,
)


def administration_permissions(request):
    critical = has_portal_permission(request.user, ADMINISTRATE_STUDENT_CRITICAL)
    sections = [
        ('query_builder', 'Vistas y Snippets', False),
        ('catalog_sync', 'Sincronización de catálogos', True),
        ('person_create', 'Alta de Persona', True),
        ('teams', 'Gestión de Teams', True),
        ('functional_create', 'Cuentas Funcionales', True),
    ]
    urls = {route: reverse('portal:' + route) for route, _, _ in sections}
    #FPORTALNAV01-9501: La ruta resalta la sección; los permisos siguen validándose en las vistas.
    active = next((route for route, _, _ in sections if request.path.startswith(urls[route])), 'administracion')
    navigation = [{'url': reverse('portal:administracion'), 'label': 'Buscador / 360°', 'active': active == 'administracion'}]
    navigation += [{'url': urls[route], 'label': label, 'active': active == route}
                   for route, label, restricted in sections if not restricted or critical]
    return {
        'can_view_360': has_portal_permission(request.user, VIEW_STUDENT_360),
        'can_operate_daily': has_portal_permission(request.user, OPERATE_STUDENT_DAILY),
        'can_administer_critical': critical,
        'admin_navigation': navigation,
    }
