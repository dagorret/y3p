"""Visibilidad derivada de las mismas capacidades que protegen el servidor."""

from apps.people.permissions import (
    ADMINISTRATE_STUDENT_CRITICAL, OPERATE_STUDENT_DAILY, VIEW_STUDENT_360, has_portal_permission,
)


def administration_permissions(request):
    return {
        'can_view_360': has_portal_permission(request.user, VIEW_STUDENT_360),
        'can_operate_daily': has_portal_permission(request.user, OPERATE_STUDENT_DAILY),
        'can_administer_critical': has_portal_permission(request.user, ADMINISTRATE_STUDENT_CRITICAL),
    }
