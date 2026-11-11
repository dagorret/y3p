"""Vistas completas y fragmentos reactivos de los portales operativos."""

from django.core.exceptions import ValidationError
from django.db.models import Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views import View

from apps.people.models import Affiliation, Identity, ProfileTypeDefinition

from .forms import ExtensionRegistrationForm, PostgradoRegistrationForm
from .services import register_affiliation
from .permissions import StaffPortalMixin
from .student_grid import student_grid_context


def _is_htmx(request):
    return request.headers.get("HX-Request", "").lower() == "true"


class AdministracionDashboardView(StaffPortalMixin, View):
    """Lista alumnos y sus vinculaciones mediante filtros y paginación en el servidor."""

    template_name = "portal/administracion.html"
    fragment_name = "portal/fragments/administracion_results.html"

    def get(self, request):
        context = student_grid_context(request.GET)
        template = self.fragment_name if _is_htmx(request) else self.template_name
        #FPORTALVIEWS001-8001: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
        return render(request, template, context)


class AdministracionSearchView(AdministracionDashboardView):
    template_name = 'portal/fragments/administracion_results.html'


class RegistrationDashboardView(View):
    """Comportamiento compartido de consulta y registro de inscripciones por área."""

    template_name = ""
    fragment_name = ""
    row_template = ""
    feedback_id = ""
    profile_code = ""
    form_class = None
    reference_field = ""
    list_context_name = ""

    def get_affiliations(self):
        return (
            Affiliation.objects.filter(profile_type__code=self.profile_code)
            .select_related(
                "identity", "identity__personal_profile", "profile_type", "current_status"
            )
            .order_by("-pk")[:50]
        )

    def get(self, request):
        context = self.get_context(self.form_class(), affiliations=self.get_affiliations())
        template = self.fragment_name if _is_htmx(request) else self.template_name
        #FPORTALVIEWS002-8002: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
        return render(request, template, context)

    def post(self, request):
        form = self.form_class(request.POST)
        if not form.is_valid():
            return self.render_invalid(request, form)

        reference = form.cleaned_data[self.reference_field]
        try:
            identity, affiliation = register_affiliation(
                profile_code=self.profile_code,
                reference=reference,
                form_data=form.cleaned_data,
            )
        #FPORTALVIEWS003-8003: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
        except ValidationError as error:
            #FPORTALVIEWS004-8004: Devuelve un error de validación sin confirmar una operación inválida.
            form.add_error(None, error)
            return self.render_invalid(request, form)

        if _is_htmx(request):
            #FPORTALVIEWS005-8005: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
            return render(
                request,
                self.row_template,
                {
                    "identity": identity,
                    "affiliation": affiliation,
                    "feedback_id": self.feedback_id,
                    "success_message": "Inscripción registrada correctamente.",
                },
            )
        #FPORTALVIEWS006-8006: Respuesta HTTP 302: mantiene el contrato de página, fragmento o redirección.
        return redirect(request.path)

    def render_invalid(self, request, form):
        if _is_htmx(request):
            #FPORTALVIEWS007-8007: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
            return render(
                request,
                "portal/fragments/registration_errors.html",
                {"form": form, "feedback_id": self.feedback_id},
            )
        context = self.get_context(form, affiliations=self.get_affiliations())
        #FPORTALVIEWS008-8008: Respuesta HTTP 422: mantiene el contrato de página, fragmento o redirección.
        return render(request, self.template_name, context, status=422)

    def get_context(self, form, *, affiliations):
        return {"form": form, "affiliations": affiliations}


class PostgradoDashboardView(RegistrationDashboardView):
    """Registra estudiantes de posgrado y actualiza la grilla reactiva."""

    template_name = "portal/postgrado.html"
    fragment_name = "portal/fragments/postgrado_content.html"
    row_template = "portal/fragments/postgrado_success.html"
    feedback_id = "postgrado-feedback"
    profile_code = "POSGRADO"
    form_class = PostgradoRegistrationForm
    reference_field = "program"


class ExtensionDashboardView(RegistrationDashboardView):
    """Registra participantes de extensión y actualiza la grilla reactiva."""

    template_name = "portal/extension.html"
    fragment_name = "portal/fragments/extension_content.html"
    row_template = "portal/fragments/extension_success.html"
    feedback_id = "extension-feedback"
    profile_code = "EXTENSION"
    form_class = ExtensionRegistrationForm
    reference_field = "course"
