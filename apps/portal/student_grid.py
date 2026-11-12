"""Generación tabular con diagnóstico SQL y optimización anti-N+1.

La grilla y Excel comparten filtros; select_related y prefetch_related cargan
relaciones en consultas constantes por página o bloque de exportación. El parser
traduce claves autorizadas a Q parametrizados y combina condiciones académicas
en EXISTS, sin SQL textual ni evaluación de código. La lista de snippets ocupa
la consulta antes dedicada a precargar eventos que la tabla ya no muestra. El
selector Moodle sustituye la precarga de cuentas que esta tabla tampoco utiliza;
el total sigue siendo siete consultas independientes del número de alumnos.
"""

from urllib.parse import urlencode

from django.apps import apps
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import BooleanField, Case, CharField, Exists, F, OuterRef, Prefetch, Q, Value, When
from django.utils import timezone
from apps.integrations.models import MoodleCourseMirror

from apps.people.models import Affiliation, DigitalAccount, Identity, OutboxEvent, SagaStatusDefinition

from apps.people.scopes import SCOPES, validate_scope, scope_url

from .query_parser import QuerySyntaxError, parse_query
from .snippet_services import resolve_query_params, visible_snippets


HEALTH_LABELS = {'green': 'Verde', 'yellow': 'Amarillo', 'orange': 'Naranja', 'red': 'Rojo'}
PAGE_SIZES = (25, 50, 100, 200)


def student_queryset(params, *, now=None, scope='GRADO'):
    """Filtra y diagnostica todas las identidades antes de recortar la página.

    EXISTS conserva una fila por identidad aunque tenga varias vinculaciones;
    carrera y estado deben coincidir en la misma vinculación. El semáforo ordena
    riesgos: fallo, vencimiento o falta de licencia conocida, advertencia y normal.
    La ausencia de observaciones nunca acredita salud. Una vigencia sin fecha es
    indefinida según el metamodelo, no un vencimiento supuesto.

    Las subconsultas evitan llamadas por persona y el recorrido completo en
    memoria. Se acepta que una observación local esté retrasada respecto de un
    proveedor para que abrir la grilla no ejecute cientos de consultas remotas.
    """
    profile_code = validate_scope(scope)
    now = now or timezone.now()
    criteria = parse_query(params.get('formula', ''), now=now)
    academic = Affiliation.objects.filter(identity_id=OuterRef('pk'), profile_type__code=profile_code)
    selected = academic.filter(criteria.affiliation)
    if params.get('career'):
        selected = selected.filter(reference=params['career'])
    if params.get('status'):
        selected = selected.filter(current_status__code=params['status'])
    official = DigitalAccount.objects.filter(identity_id=OuterRef('pk'), platform__in=['TEAMS', 'GOOGLE'], is_active=True)
    moodle = DigitalAccount.objects.filter(identity_id=OuterRef('pk'), platform='MOODLE', is_active=True,
        observations__has_key='courses').exclude(observations__courses=[]).exclude(observations__courses=None)
    qs = Identity.objects.annotate(
        _selected=Exists(selected), _active=Exists(academic.filter(is_active=True)),
        _expired=Exists(academic.filter(is_active=True, expires_at__lte=now)),
        _failed=Exists(OutboxEvent.objects.filter(identity_id=OuterRef('pk'), affiliation__profile_type__code=profile_code, status='FAILED')),
        _moodle_failed=Exists(OutboxEvent.objects.filter(identity_id=OuterRef('pk'), affiliation__profile_type__code=profile_code, step_results__moodle__status='FAILED')),
        _account=Exists(official),
        _license_known=Exists(official.filter(observations__has_key='license_assigned')),
        _licensed=Exists(official.filter(observations__license_assigned=True)),
        _temporary=Exists(official.filter(observations__force_password_change=True)),
        _activity=Exists(official.filter(observations__last_activity_at__isnull=False)
                         .exclude(observations__last_activity_at='').exclude(observations__last_activity_at=None)),
        _enrolled=Exists(moodle),
    ).filter(_selected=True)
    if settings.ENABLE_LOCAL_MOCKS and apps.is_installed('apps.platform_mocks'):
        Azure = apps.get_model('platform_mocks', 'MockAzureUser')
        Enrollment = apps.get_model('platform_mocks', 'MockMoodleEnrollment')
        azure = Azure.objects.filter(upn=OuterRef('institutional_upn'))
        qs = qs.annotate(_mock=Exists(azure))
        from apps.platform_mocks import DEV_A1_SKU
        sku = settings.LUCY_M365_SKU_ID or DEV_A1_SKU
        observations = {
            '_account': Exists(azure.filter(is_active=True)),
            '_license_known': Value(bool(sku), output_field=BooleanField()),
            '_licensed': Exists(azure.filter(assigned_sku=sku)) if sku else Value(False, output_field=BooleanField()),
            '_temporary': Exists(azure.filter(password_profile_force_change=True)),
            '_activity': Exists(azure.filter(last_activity_at__isnull=False)),
            '_enrolled': Exists(Enrollment.objects.filter(user__username=OuterRef('institutional_upn'), user__suspended=False, role_id=5)),
        }
        qs = qs.annotate(**{field: Case(When(_mock=True, then=value), default=F(field), output_field=BooleanField())
                            for field, value in observations.items()})
    qs = qs.annotate(health=Case(
        When(Q(_failed=True) | Q(_account=True, _moodle_failed=True), then=Value('red')),
        When(Q(_expired=True) | Q(_active=True, _license_known=True, _licensed=False), then=Value('orange')),
        When(_account=True, _licensed=True, _enrolled=True, _active=True, _temporary=False, _activity=True, then=Value('green')),
        default=Value('yellow'), output_field=CharField(),
    ))
    query = params.get('q', '').strip()
    if query:
        qs = qs.filter(Q(identifier__icontains=query) | Q(first_name__icontains=query)
                       | Q(last_name__icontains=query) | Q(institutional_upn__icontains=query))
    if params.get('health') in HEALTH_LABELS:
        qs = qs.filter(health=params['health'])
    qs = qs.filter(criteria.identity)
    #FGRID01-8001: Las relaciones simples se unen; las colecciones se precargan por página.
    # La precarga evita consultas N+1; los errores Outbox ya están resumidos por EXISTS.
    return qs.select_related('personal_profile').prefetch_related(
        Prefetch('affiliations', queryset=Affiliation.objects.filter(profile_type__code=profile_code).select_related('profile_type', 'current_status'), to_attr='grade_affiliations'),
    ).order_by('last_name', 'first_name', 'pk')


def student_grid_context(params, *, user=None, feedback='', error=False, builder_mode=False, scope='GRADO'):
    """Materializa hasta 200 filas con siete consultas y relaciones precargadas.

    La paginación ocurre después del diagnóstico SQL; filtrar por salud abarca
    todo el padrón. La explicación de cada fila usa anotaciones ya obtenidas y
    no dispara consultas adicionales. Los enlaces conservan los filtros vigentes.
    """
    profile_code = validate_scope(scope)
    snippets = visible_snippets(user, scope=scope)
    selected_snippet = None
    query_error = ''
    try:
        params, selected_snippet = resolve_query_params(params, snippets)
        queryset = student_queryset(params, scope=scope)
    except QuerySyntaxError as exc:
        #FGRID03-8303: La fórmula inválida se muestra sin ampliar silenciosamente el resultado.
        query_error = '; '.join(exc.messages)
        queryset = Identity.objects.none()
    #FGRID02-8002: Solo se aceptan tamaños acotados; cualquier otro usa 50.
    page_size = str(params.get('page_size', '50'))
    page_size = int(page_size) if page_size in {str(size) for size in PAGE_SIZES} else 50
    page = Paginator(queryset, page_size).get_page(params.get('page'))
    identities = list(page.object_list)
    describe_health(identities)
    query = {key: params[key] for key in ('q', 'career', 'status', 'health', 'formula') if params.get(key)}
    if query_error and params.get('snippet'):
        query['snippet'] = params['snippet']
    query['page_size'] = page_size
    grid_url = scope_url(scope, 'builder' if builder_mode else 'grid')
    for identity in identities:
        identity.detail_url = scope_url(scope, 'detail', identity_id=identity.pk)
    def link(number):
        return grid_url + '?' + urlencode({**query, 'page': number})
    return {
        'scope': scope, 'scope_title': SCOPES[scope][1], 'is_scoped_academic': scope != 'GRADO',
        'export_url': scope_url(scope, 'export'), 'bulk_url': scope_url(scope, 'bulk'),
        'snippet_create_url': scope_url(scope, 'snippet_create'),
        'builder_mode': builder_mode, 'grid_url': grid_url,
        'moodle_courses': list(MoodleCourseMirror.objects.all()),
        'page_links': [{'number': number, 'ellipsis': number == page.paginator.ELLIPSIS,
                        'current': number == page.number,
                        'url': '' if number == page.paginator.ELLIPSIS else link(number)}
                       for number in page.paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1)],
        'snippets': snippets, 'selected_snippet': selected_snippet, 'query_error': query_error,
        'formula': params.get('formula', ''),
        'identities': identities, 'page_obj': page, 'query': params.get('q', ''),
        'page_size': page_size, 'page_sizes': PAGE_SIZES,
        'export_query': urlencode(query), 'filters': query, 'health_options': HEALTH_LABELS.items(),
        'careers': list(Affiliation.objects.filter(profile_type__code=profile_code).exclude(reference='').order_by('reference').values_list('reference', flat=True).distinct()),
        'saga_states': list(SagaStatusDefinition.objects.filter(profile_type__code=profile_code).order_by('code').values_list('code', 'name')),
        'previous_url': link(page.previous_page_number()) if page.has_previous() else '',
        'next_url': link(page.next_page_number()) if page.has_next() else '',
        'feedback': feedback, 'error': error,
    }


def describe_health(identities):
    """Explica el diagnóstico usando exclusivamente anotaciones precargadas."""
    for identity in identities:
        reasons = []
        if identity._failed:
            reasons.append('Hay eventos FAILED en el Outbox')
        if identity._account and identity._moodle_failed:
            reasons.append('Cuenta oficial presente con fallo en Moodle')
        if identity._expired:
            reasons.append('Vigencia alcanzada o vencida')
        if identity._active and identity._license_known and not identity._licensed:
            reasons.append('Estado activo sin licencia A1')
        if identity._temporary:
            reasons.append('Clave temporal pendiente de cambio')
        if not identity._activity:
            reasons.append('Sin actividad registrada')
        if not identity._account:
            reasons.append('Sin cuenta oficial activa confirmada')
        if not identity._license_known:
            reasons.append('Sin observación de licencia A1')
        if not identity._enrolled:
            reasons.append('Sin matrícula Moodle confirmada')
        if not identity._active:
            reasons.append('Sin vinculación académica activa')
        identity.health_label = HEALTH_LABELS[identity.health]
        identity.health_reason = '; '.join(reasons) or 'Cuenta activa, licencia, matrícula y actividad confirmadas; vigencia válida.'
