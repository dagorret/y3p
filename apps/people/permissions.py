"""Capacidades jerárquicas del portal, independientes del CRUD de Unfold."""

VIEW_STUDENT_360 = 'people.can_view_student_360'
OPERATE_STUDENT_DAILY = 'people.can_operate_student_daily'
ADMINISTRATE_STUDENT_CRITICAL = 'people.can_administrate_student_critical'

_GRANTS = {
    VIEW_STUDENT_360: (VIEW_STUDENT_360, OPERATE_STUDENT_DAILY, ADMINISTRATE_STUDENT_CRITICAL),
    OPERATE_STUDENT_DAILY: (OPERATE_STUDENT_DAILY, ADMINISTRATE_STUDENT_CRITICAL),
    ADMINISTRATE_STUDENT_CRITICAL: (ADMINISTRATE_STUDENT_CRITICAL,),
}


def has_portal_permission(user, permission):
    """Resuelve la jerarquía institucional sin conceder permisos CRUD amplios.

    Lectura permite consultar; operación diaria agrega soporte; administración
    crítica incluye ambos niveles y las decisiones de mayor impacto. Un permiso
    superior implica los inferiores aun si fue asignado directamente, sin grupo.
    La misma función gobierna el servidor y la visibilidad. Los grupos no alteran
    is_staff: acceder al portal no habilita por sí solo el administrador Unfold.
    """
    if not user.is_authenticated or not user.is_active:
        return False
    return any(user.has_perm(candidate) for candidate in _GRANTS.get(permission, ()))
