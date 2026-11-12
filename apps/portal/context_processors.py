"""Visibilidad derivada de las mismas capacidades que protegen el servidor."""

from django.urls import reverse
from apps.people.scopes import SCOPES, has_scope_permission, scope_url



def administration_permissions(request):
    scope = getattr(request, 'academic_scope', 'GRADO')
    critical = has_scope_permission(request.user, scope, 'admin')
    sections = [
        ('query_builder', 'Vistas y Snippets', False),
        ('catalog_sync', 'Sincronización de catálogos', True),
        ('person_create', 'Alta de Persona', True),
        ('teams', 'Gestión de Teams', True),
        ('functional_create', 'Cuentas Funcionales', True),
        ('outbox_monitor', 'Monitor Outbox', False),
    ]
    urls = {route: reverse('portal:' + route) for route, _, _ in sections}
    #FPORTALNAV01-9501: La ruta resalta la sección; los permisos siguen validándose en las vistas.
    active = next((route for route, _, _ in sections if request.path.startswith(urls[route])), 'administracion')
    navigation = [{'url': scope_url(code), 'label': 'Buscador / 360°' if code == 'GRADO' else definition[1],
                   'active': request.path.startswith(scope_url(code)) and (code != 'GRADO' or active == 'administracion') and '/vistas/' not in request.path}
                  for code, definition in SCOPES.items() if has_scope_permission(request.user, code)]
    if scope == 'GRADO':
        navigation += [{'url': urls[route], 'label': label, 'active': active == route}
                       for route, label, restricted in sections if route != 'outbox_monitor' and (not restricted or critical)]
    else:
        navigation += [{'url': scope_url(scope, 'builder'), 'label': 'Vistas y Snippets', 'active': request.path.startswith(scope_url(scope, 'builder'))}]
    can_monitor = any(has_scope_permission(request.user, code, 'operate') for code in SCOPES)
    if can_monitor:
        navigation += [{'url': urls['outbox_monitor'], 'label': 'Monitor Outbox', 'active': active == 'outbox_monitor', 'outbox': True}]
    return {
        'can_view_360': has_scope_permission(request.user, scope, 'view'),
        'can_operate_daily': has_scope_permission(request.user, scope, 'operate'),
        'can_administer_critical': critical,
        'admin_navigation': navigation,
    }
