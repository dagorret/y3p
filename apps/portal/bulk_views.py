"""Vista de comandos: valida permisos y delega al Outbox transaccional.

La respuesta tabular reutiliza precargas anti-N+1 y confirma solo la persistencia
del lote; el trabajador despacha los correos después de confirmar PostgreSQL.
"""

from django.core.exceptions import ValidationError
from django.shortcuts import render
from django.views import View

from apps.people.permissions import ADMINISTRATE_STUDENT_CRITICAL, OPERATE_STUDENT_DAILY
from .bulk_services import enqueue_student_actions
from .permissions import StaffPortalMixin
from .student_grid import student_grid_context


class StudentBulkActionView(StaffPortalMixin, View):
    def get_required_permission(self):
        return ADMINISTRATE_STUDENT_CRITICAL if self.request.POST.get('action') == 'license' else OPERATE_STUDENT_DAILY

    def post(self, request):
        error = False
        try:
            ids = [int(value) for value in request.POST.getlist('identity_ids')]
            batch, events = enqueue_student_actions(ids, request.POST.get('action'), user=request.user,
                options={key: request.POST.get(key, '') for key in ('shortname', 'role_id', 'group_name', 'subject', 'body')})
            feedback = f'Lote {batch}: {len(events)} solicitudes registradas en el Outbox. Se procesarán en segundo plano.'
        #FPORTALBULKVIEWS001-8001: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
        except (ValidationError, ValueError) as exc:
            error = True
            feedback = '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'La selección no es válida.'
        #FPORTALBULKVIEWS002-8002: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
        return render(request, 'portal/fragments/administracion_results.html',
                      student_grid_context(request.POST, user=request.user, feedback=feedback, error=error))
