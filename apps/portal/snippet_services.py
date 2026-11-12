"""Vistas guardadas: visibilidad por autor y traducción segura a ORM.

Los filtros estructurados usan una lista blanca y las fórmulas se recompilan al
crear y al consultar. Nunca se persisten consultas SQL. La lista visible se carga
una sola vez y sirve tanto al selector como a la resolución del snippet elegido.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q

from apps.people.permissions import ADMINISTRATE_STUDENT_CRITICAL, OPERATE_STUDENT_DAILY, has_portal_permission
from apps.people.support import require_permission
from .models import AdminQuerySnippet
from .query_parser import QuerySyntaxError, parse_query


FILTER_KEYS = {'q', 'career', 'status', 'health', 'page_size'}


def visible_snippets(user):
    """Carga solo vistas compartidas o propias; no expone vistas privadas ajenas."""
    visible = Q(pk=0)
    if user is not None and user.is_authenticated:
        visible = Q(is_shared=True) | Q(created_by_id=user.pk)
    snippets = list(AdminQuerySnippet.objects.filter(visible))
    administrator = user is not None and has_portal_permission(user, ADMINISTRATE_STUDENT_CRITICAL)
    for snippet in snippets:
        snippet.can_delete = administrator or (user is not None and snippet.created_by_id == user.pk)
    return snippets


def validate_filter_params(params):
    """Valida el esquema JSON sin admitir rutas ORM ni parámetros de navegación."""
    #FSNIPPET01-8201: El esquema complementario es un objeto de claves y textos acotados.
    if not isinstance(params, dict) or set(params) - FILTER_KEYS or any(
        not isinstance(value, str) or len(value) > 200 for value in params.values()
    ):
        raise ValidationError('Los filtros complementarios deben ser textos de hasta 200 caracteres con claves q, career, status, health o page_size.')
    #FSNIPPET02-8202: El tamaño y el semáforo pertenecen a catálogos cerrados.
    if params.get('page_size', '50') not in {'25', '50', '100', '200'}:
        raise ValidationError('El tamaño de página debe ser 25, 50, 100 o 200.')
    if params.get('health', '') not in {'', 'green', 'yellow', 'orange', 'red'}:
        raise ValidationError('El filtro de salud no es válido.')
    return params


def resolve_query_params(params, snippets):
    """Resuelve la selección únicamente sobre la lista ya autorizada y cargada."""
    resolved = dict(params.items())
    selected = None
    if params.get('snippet'):
        selected = next((snippet for snippet in snippets if str(snippet.pk) == str(params['snippet'])), None)
        if selected is None:
            #FSNIPPET03-8203: Una referencia inexistente o privada ajena tiene el mismo error.
            raise QuerySyntaxError('La vista guardada no existe o no está disponible para su usuario.')
        try:
            complementary = validate_filter_params(selected.filter_params)
        except ValidationError:
            #FSNIPPET04-8204: Incluso datos persistidos fuera del servicio se validan antes de usarse.
            raise QuerySyntaxError('La vista guardada contiene filtros complementarios inválidos.') from None
        resolved = {**complementary, 'formula': selected.query_expression}
    return resolved, selected


@transaction.atomic
def create_snippet(*, user, name, expression, filter_params, is_shared=False):
    """Guarda una preferencia validada; el autor siempre es el usuario autenticado."""
    require_permission(user, OPERATE_STUDENT_DAILY)
    #FSNIPPET05-8205: El nombre identifica la vista y no puede estar vacío.
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 150:
        raise ValidationError('Indique un nombre de hasta 150 caracteres para la vista guardada.')
    parse_query(expression)
    validate_filter_params(filter_params)
    snippet = AdminQuerySnippet(name=name.strip(), query_expression=expression.strip(),
                                filter_params=filter_params, is_shared=is_shared, created_by=user)
    snippet.full_clean()
    snippet.save()
    return snippet


@transaction.atomic
def delete_snippet(*, user, snippet_id):
    """Permite borrar vistas propias y concede alcance global a administración."""
    require_permission(user, OPERATE_STUDENT_DAILY)
    allowed = AdminQuerySnippet.objects.filter(pk=snippet_id)
    if not has_portal_permission(user, ADMINISTRATE_STUDENT_CRITICAL):
        allowed = allowed.filter(created_by=user)
    if not allowed.delete()[0]:
        #FSNIPPET06-8206: No revela existencia ni autor de una vista privada ajena.
        raise PermissionDenied('No tiene permiso para eliminar esta vista guardada.')
