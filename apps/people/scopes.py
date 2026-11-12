"""Aislamiento académico por ámbito, independiente de los filtros del navegador.

Cada capacidad autoriza solamente el ámbito declarado. Los permisos de Grado
mantienen sus nombres históricos; Posgrado y Extensión poseen capacidades
propias. El superusuario conserva acceso institucional explícito a los tres.
"""

from django.core.exceptions import PermissionDenied
from django.urls import reverse

from .permissions import VIEW_STUDENT_360, OPERATE_STUDENT_DAILY, ADMINISTRATE_STUDENT_CRITICAL, has_portal_permission

SCOPES = {'GRADO': ('ALUMNO', 'Administración de alumnos', 'administracion'),
          'POSGRADO': ('POSGRADO', 'Posgrado', 'postgrado'),
          'EXTENSION': ('EXTENSION', 'Extensión', 'extension')}
LEVELS = {'view': VIEW_STUDENT_360, 'operate': OPERATE_STUDENT_DAILY, 'admin': ADMINISTRATE_STUDENT_CRITICAL}


def validate_scope(scope):
    """Resuelve únicamente códigos institucionales presentes en la lista blanca."""
    #FSCOPE01-10001: El ámbito no es un nombre de modelo ni un campo ORM proporcionado por el cliente.
    if scope not in SCOPES:
        raise PermissionDenied('El ámbito académico no está autorizado.')
    return SCOPES[scope][0]


def has_scope_permission(user, scope, level='view'):
    """Evalúa la jerarquía de capacidades dentro de un único ámbito."""
    if scope not in SCOPES or level not in LEVELS or not user.is_authenticated or not user.is_active:
        return False
    if scope == 'GRADO':
        return has_portal_permission(user, LEVELS[level])
    ranks = ('view', 'operate', 'admin')
    return any(user.has_perm(f'people.can_{rank}_{scope.lower()}') for rank in ranks[ranks.index(level):])


def require_scope_permission(user, scope, level='view'):
    """Rechaza accesos cruzados antes de consultar datos o iniciar operaciones."""
    #FSCOPE02-10002: La autorización se comprueba también en servicios y trabajadores, no solo en HTML.
    if not has_scope_permission(user, scope, level):
        raise PermissionDenied('No tiene permiso para operar en este ámbito académico.')


def scope_url(scope, operation='grid', **kwargs):
    """Genera enlaces que mantienen el ámbito autorizado entre las pantallas."""
    validate_scope(scope)
    grade = {'grid': 'administracion', 'export': 'student_export', 'bulk': 'student_bulk_action',
             'detail': 'identity360', 'builder': 'query_builder', 'snippet_create': 'snippet_create', 'snippet_delete': 'snippet_delete'}
    if scope == 'GRADO':
        name = grade[operation]
    elif operation == 'grid':
        name = SCOPES[scope][2]
    else:
        name = f'{SCOPES[scope][2]}_{operation}'
    return reverse('portal:' + name, kwargs=kwargs or None)
