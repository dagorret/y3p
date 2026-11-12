"""Control de acceso del área administrativa de ventanilla."""

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from apps.people.permissions import VIEW_STUDENT_360, OPERATE_STUDENT_DAILY, ADMINISTRATE_STUDENT_CRITICAL
from apps.people.scopes import require_scope_permission


class StaffPortalMixin:
    """Aplica autorización a todas las solicitudes antes de resolver el objeto.

    La autenticación precede a los permisos. Un usuario autenticado sin capacidad
    recibe 403 incluso si intenta invocar directamente un formulario oculto. La
    visibilidad del navegador es una ayuda operativa, no un control de seguridad.
    El nombre histórico de la clase no exige is_staff: se evalúan capacidades.
    """
    scope = 'GRADO'
    required_permission = VIEW_STUDENT_360

    def get_required_permission(self):
        return self.required_permission

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            #FPORTALPERMISSIONS001-8001: Respuesta HTTP 302: mantiene el contrato de página, fragmento o redirección.
            return redirect_to_login(request.get_full_path(), '/dash/administracion/ingresar/')
        level = {VIEW_STUDENT_360: 'view', OPERATE_STUDENT_DAILY: 'operate', ADMINISTRATE_STUDENT_CRITICAL: 'admin'}[self.get_required_permission()]
        require_scope_permission(request.user, self.scope, level)
        #FPORTALSCOPE01-10003: Un parámetro de cliente no puede cambiar el ámbito fijado por la ruta.
        if request.GET.get('scope', self.scope) != self.scope or request.POST.get('scope', self.scope) != self.scope:
            raise PermissionDenied('No puede cambiar el ámbito de esta operación.')
        request.academic_scope = self.scope
        return super().dispatch(request, *args, **kwargs)
