"""Control de acceso del área administrativa de ventanilla."""

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from apps.people.permissions import VIEW_STUDENT_360, has_portal_permission


class StaffPortalMixin:
    """Aplica autorización a todas las solicitudes antes de resolver el objeto.

    La autenticación precede a los permisos. Un usuario autenticado sin capacidad
    recibe 403 incluso si intenta invocar directamente un formulario oculto. La
    visibilidad del navegador es una ayuda operativa, no un control de seguridad.
    El nombre histórico de la clase no exige is_staff: se evalúan capacidades.
    """
    required_permission = VIEW_STUDENT_360

    def get_required_permission(self):
        return self.required_permission

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            #FPORTALPERMISSIONS001-8001: Respuesta HTTP 302: mantiene el contrato de página, fragmento o redirección.
            return redirect_to_login(request.get_full_path(), '/dash/administracion/ingresar/')
        if not has_portal_permission(request.user, self.get_required_permission()):
            #FPORTALPERMISSIONS002-8002: Propaga el fallo original sin convertirlo en éxito.
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
