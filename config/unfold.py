"""Django Unfold configuration for the Y3P technical backoffice."""

from django.templatetags.static import static
from django.urls import reverse_lazy


UNFOLD = {
    "SITE_TITLE": "Y3P — FCE UNRC",
    "SITE_HEADER": "Y3P — FCE UNRC",
    "SITE_NAME": "Y3P — FCE UNRC",
    "STYLES": [lambda request: static("css/admin.css")],
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Operación",
                "separator": True,
                "items": [
                    {
                        "title": "Outbox",
                        "icon": "outbox",
                        "link": reverse_lazy("admin:people_outboxevent_changelist"),
                        "permission": lambda request: request.user.has_perm(
                            "people.view_outboxevent"
                        ),
                    },
                    {
                        "title": "Personas",
                        "icon": "people",
                        "link": reverse_lazy("admin:people_identity_changelist"),
                        "permission": lambda request: request.user.has_perm(
                            "people.view_identity"
                        ),
                    },
                    {
                        "title": "Vinculaciones",
                        "icon": "badge",
                        "link": reverse_lazy("admin:people_affiliation_changelist"),
                        "permission": lambda request: request.user.has_perm(
                            "people.view_affiliation"
                        ),
                    },
                ],
            },
            {
                "title": "Metamodelo / Configuración Crítica",
                "separator": True,
                "items": [
                    {
                        "title": "Tipos de perfil",
                        "icon": "settings",
                        "link": reverse_lazy("admin:people_profiletypedefinition_changelist"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Estados de saga",
                        "icon": "account_tree",
                        "link": reverse_lazy("admin:people_sagastatusdefinition_changelist"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Reglas de transición",
                        "icon": "rule",
                        "link": reverse_lazy("admin:people_sagatransitionrule_changelist"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Restricciones cruzadas",
                        "icon": "hub",
                        "link": reverse_lazy("admin:people_crossprofileconstraint_changelist"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                ],
            },
        ],
    },
}
