"""Observabilidad no bloqueante del Outbox y rearme con trazabilidad del operador.

Los conteos usan agregaciones SQL de solo lectura y el badge se actualiza en
una solicitud HTMX separada, sin cargar eventos por alumno ni alterar las siete
consultas de la grilla. El monitor filtra siempre por ámbitos autorizados.
El rearme toma bloqueos breves, conserva checkpoints confirmados y actor original,
y registra al administrador que habilitó el nuevo presupuesto de reintentos.
"""

import json
from urllib.parse import urlencode

from celery import current_app
from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import render
from django.views import View

from apps.people.models import OutboxEvent
from apps.people.scopes import SCOPES, has_scope_permission, require_scope_permission


def authorized_scopes(user, level='operate'):
    return [scope for scope in SCOPES if has_scope_permission(user, scope, level)]


def visible_events(user):
    scopes = authorized_scopes(user)
    if 'GRADO' in scopes:
        #FMONITOR08-10414: El portal histórico conserva tipos institucionales, pero excluye ámbitos académicos no concedidos.
        excluded = [SCOPES[scope][0] for scope in ('POSGRADO', 'EXTENSION') if scope not in scopes]
        return OutboxEvent.objects.exclude(affiliation__profile_type__code__in=excluded)
    profiles = [SCOPES[scope][0] for scope in scopes]
    return OutboxEvent.objects.filter(affiliation__profile_type__code__in=profiles)


@transaction.atomic
def rearm_failed_events(event_ids, *, user):
    #FMONITOR06-10412: Incluso un POST vacío de rearme requiere capacidad crítica.
    if not authorized_scopes(user, 'admin'):
        raise PermissionDenied('El rearme requiere permisos de administración crítica.')
    ids = sorted(set(event_ids))
    #FMONITOR01-10401: El límite evita operaciones masivas sin cota desde el navegador.
    if not 1 <= len(ids) <= 200:
        raise ValidationError('Seleccione entre 1 y 200 eventos.')
    events = list(OutboxEvent.objects.select_for_update(of=('self',)).filter(pk__in=ids)
                  .select_related('affiliation__profile_type').order_by('pk'))
    #FMONITOR02-10402: Se valida el conjunto completo antes de rearmar la primera fila.
    if len(events) != len(ids):
        raise PermissionDenied('La selección contiene eventos no autorizados.')
    for event in events:
        scope = next((scope for scope, definition in SCOPES.items() if definition[0] == event.affiliation.profile_type.code), 'GRADO')
        require_scope_permission(user, scope, 'admin')
    updated = skipped = 0
    for event in events:
        if event.status != 'FAILED':
            skipped += 1
            continue
        previous_attempts = event.attempts
        #FMONITOR03-10403: Solo FAILED vuelve a PENDING; checkpoints y solicitante original se conservan.
        event.status, event.attempts, event.last_error = 'PENDING', 0, ''
        event.locked_at = event.heartbeat_at = event.retry_after = event.processed_at = None
        event.save(update_fields=['status', 'attempts', 'last_error', 'locked_at', 'heartbeat_at', 'retry_after', 'processed_at'])
        LogEntry.objects.log_actions(user_id=user.pk, queryset=[event], action_flag=CHANGE,
            change_message=json.dumps({'accion': 'rearme_outbox', 'estado_anterior': 'FAILED', 'estado_nuevo': 'PENDING',
                                       'intentos_anteriores': previous_attempts}, ensure_ascii=False))
        updated += 1
    if updated:
        transaction.on_commit(lambda: current_app.send_task('provisioning.dispatch_outbox', queue='provisioning'), robust=True)
    return updated, skipped


class OutboxAccessMixin:
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            #FMONITORHTTP01-10404: La autenticación precede a la consulta del monitor.
            return redirect_to_login(request.get_full_path(), '/dash/administracion/ingresar/')
        scopes = authorized_scopes(request.user)
        if not scopes:
            #FMONITORHTTP02-10405: Lectura general de fichas no concede acceso operativo al Outbox.
            raise PermissionDenied('El monitor requiere permisos de operación académica.')
        #FMONITOR07-10413: Un intento explícito de consultar otro ámbito se rechaza antes del conteo.
        if request.GET.get('scope', scopes[0]) not in scopes or request.POST.get('scope', scopes[0]) not in scopes:
            raise PermissionDenied('No tiene permiso para consultar el Outbox de ese ámbito.')
        request.academic_scope = scopes[0]
        return super().dispatch(request, *args, **kwargs)


class OutboxBadgeView(OutboxAccessMixin, View):
    def get(self, request):
        count = visible_events(request.user).filter(status__in=['PENDING', 'RETRY_SCHEDULED', 'FAILED']).count()
        #FMONITORHTTP03-10406: Conteo local actualizado sin bloquear filas ni consultar proveedores.
        return render(request, 'portal/fragments/outbox_badge.html', {'outbox_count': count})


class OutboxMonitorView(OutboxAccessMixin, View):
    def show(self, request, *, feedback='', error=False, params=None):
        base = visible_events(request.user)
        counts = base.aggregate(**{state: Count('pk', filter=Q(status=state)) for state in ('PENDING', 'PROCESSING', 'SUCCEEDED', 'FAILED')})
        params = params if params is not None else request.GET if request.method == 'GET' else request.POST
        query = {key: params[key] for key in ('status', 'action') if params.get(key)}
        #FMONITOR04-10407: Estado y operación deben pertenecer al catálogo cerrado del modelo.
        if query.get('status', '') not in {'', *OutboxEvent.Status.values} or query.get('action', '') not in {'', 'PROVISION', 'SUSPEND', 'TERMINATE', 'SUPPORT'}:
            raise ValidationError('El estado o la operación del monitor no son válidos.')
        events = base.filter(**query).select_related('identity', 'affiliation__profile_type').order_by('-created_at', '-pk')
        page = Paginator(events, 50).get_page(params.get('page'))
        links = [{'number': number, 'current': number == page.number, 'ellipsis': number == page.paginator.ELLIPSIS,
                  'url': '?' + urlencode({**query, 'page': number})} for number in page.paginator.get_elided_page_range(page.number)]
        #FMONITORHTTP04-10408: El listado paginado carga identidad y ámbito en la misma consulta.
        return render(request, 'portal/fragments/outbox_monitor.html' if request.headers.get('HX-Request') == 'true' else 'portal/administracion/outbox.html', {
            'counts': counts, 'events_page': page, 'page_links': links, 'feedback': feedback, 'error': error,
            'status_options': OutboxEvent.Status.choices, 'action_options': OutboxEvent._meta.get_field('action').choices,
            'monitor_filters': query, 'can_rearm': bool(authorized_scopes(request.user, 'admin'))})

    def get(self, request):
        try:
            return self.show(request)
        except ValidationError as exc:
            #FMONITORHTTP05-10409: El filtro inválido produce respuesta 400 legible en lugar de error 500.
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest('; '.join(exc.messages))

    def post(self, request):
        try:
            #FMONITOR05-10411: Valida filtros antes de cambiar eventos, también en peticiones POST manipuladas.
            if request.POST.get('status', '') not in {'', *OutboxEvent.Status.values} or request.POST.get('action', '') not in {'', 'PROVISION', 'SUSPEND', 'TERMINATE', 'SUPPORT'}:
                raise ValidationError('El estado o la operación del monitor no son válidos.')
            updated, skipped = rearm_failed_events([int(value) for value in request.POST.getlist('event_ids')], user=request.user)
            return self.show(request, feedback=f'Eventos rearmados: {updated}. Omitidos: {skipped}.')
        except (ValidationError, ValueError) as exc:
            #FMONITORHTTP06-10410: Una selección mal formada no inicia ningún rearme parcial.
            feedback = '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'La selección de eventos no es válida.'
            return self.show(request, feedback=feedback, error=True, params={})
