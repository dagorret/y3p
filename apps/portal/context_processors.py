"""Navegación única por ámbitos autorizados y herramientas del ámbito activo.

La cabecera contiene los selectores; el lateral contiene operaciones, nunca
una segunda lista de ámbitos. Esta visibilidad reutiliza los permisos del
servidor y no agrega consultas de alumnos ni conteos síncronos del Outbox.
"""

from urllib.parse import urlencode

from django.urls import reverse
from apps.people.scopes import SCOPES, has_scope_permission, scope_url


def administration_permissions(request):
    scope = getattr(request, 'academic_scope', 'GRADO')
    allowed = [code for code in SCOPES if has_scope_permission(request.user, code)]
    if scope not in allowed and allowed:
        scope = allowed[0]
    #FNAVSCOPE01-10701: La cabecera publica únicamente ámbitos autorizados por capacidades reales.
    scope_navigation = [{'url': scope_url(code), 'label': 'Administración / Grado' if code == 'GRADO' else SCOPES[code][1],
                         'scope': code, 'active': code == scope} for code in allowed]
    critical = has_scope_permission(request.user, scope, 'admin')
    operate = has_scope_permission(request.user, scope, 'operate')
    navigation = []
    if scope in allowed:
        tools = [(scope_url(scope), 'Buscador / 360°'), (scope_url(scope, 'builder'), 'Vistas y Snippets')]
        if scope == 'GRADO' and critical:
            tools += [(reverse('portal:catalog_sync'), 'Sincronización de catálogos'),
                      (reverse('portal:person_create'), 'Alta / Inscripción'),
                      (reverse('portal:teams'), 'Gestión de Teams'),
                      (reverse('portal:functional_create'), 'Cuentas Funcionales')]
        elif scope != 'GRADO' and operate:
            tools += [(reverse(f'portal:{SCOPES[scope][2]}_registration'), 'Alta / Inscripción')]
        if operate:
            tools += [(reverse('portal:outbox_monitor'), 'Monitor Outbox')]
        current = next((url for url, _ in reversed(tools[1:]) if request.path.startswith(url)), tools[0][0])
        #FNAVSCOPE02-10702: El lateral tiene un único buscador y las herramientas válidas del ámbito activo.
        for url, label in tools:
            outbox = label == 'Monitor Outbox'
            query = '' if request.user.is_superuser else '?' + urlencode({'scope': scope})
            navigation.append({'url': url + query if outbox else url, 'label': label,
                               'active': url == current, 'outbox': outbox})
    badge_url = reverse('portal:outbox_badge')
    if not request.user.is_superuser:
        badge_url += '?' + urlencode({'scope': scope})
    return {
        'can_view_360': has_scope_permission(request.user, scope, 'view'),
        'can_operate_daily': operate, 'can_administer_critical': critical,
        'scope_navigation': scope_navigation, 'admin_navigation': navigation,
        'portal_home_url': scope_url(scope) if allowed else reverse('portal:administracion_login'),
        'outbox_badge_url': badge_url,
    }
