"""HTMX and full-page views for the three public operational dashboards."""

from django.core.exceptions import ValidationError
from django.db.models import Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views import View

from apps.people.models import Affiliation, Identity, ProfileTypeDefinition

from .forms import ExtensionRegistrationForm, PostgradoRegistrationForm
from .services import register_affiliation


def _is_htmx(request):
    return request.headers.get("HX-Request", "").lower() == "true"


class AdministracionDashboardView(View):
    """Search grade identities and their current grade affiliations."""

    template_name = "portal/administracion.html"
    fragment_name = "portal/fragments/administracion_results.html"

    def get(self, request):
        query = request.GET.get("q", "").strip()
        identities = Identity.objects.none()
        if query:
            grade_affiliations = Affiliation.objects.filter(
                profile_type__code="ALUMNO"
            ).select_related("profile_type", "current_status")
            identities = (
                Identity.objects.filter(
                    Q(identifier__icontains=query)
                    | Q(first_name__icontains=query)
                    | Q(last_name__icontains=query),
                    affiliations__profile_type__code="ALUMNO",
                )
                .distinct()
                .prefetch_related(
                    Prefetch("affiliations", queryset=grade_affiliations, to_attr="grade_affiliations")
                )
                .order_by("last_name", "first_name")[:50]
            )
        context = {"query": query, "identities": identities}
        template = self.fragment_name if _is_htmx(request) else self.template_name
        return render(request, template, context)


class RegistrationDashboardView(View):
    """Shared GET/POST behavior for a program-specific registration screen."""

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
        except ValidationError as error:
            form.add_error(None, error)
            return self.render_invalid(request, form)

        if _is_htmx(request):
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
        return redirect(request.path)

    def render_invalid(self, request, form):
        if _is_htmx(request):
            return render(
                request,
                "portal/fragments/registration_errors.html",
                {"form": form, "feedback_id": self.feedback_id},
            )
        context = self.get_context(form, affiliations=self.get_affiliations())
        return render(request, self.template_name, context, status=422)

    def get_context(self, form, *, affiliations):
        return {"form": form, "affiliations": affiliations}


class PostgradoDashboardView(RegistrationDashboardView):
    """Register postgraduate students and append them to the live grid."""

    template_name = "portal/postgrado.html"
    fragment_name = "portal/fragments/postgrado_content.html"
    row_template = "portal/fragments/postgrado_success.html"
    feedback_id = "postgrado-feedback"
    profile_code = "POSGRADO"
    form_class = PostgradoRegistrationForm
    reference_field = "program"


class ExtensionDashboardView(RegistrationDashboardView):
    """Register extension course participants and append them to the live grid."""

    template_name = "portal/extension.html"
    fragment_name = "portal/fragments/extension_content.html"
    row_template = "portal/fragments/extension_success.html"
    feedback_id = "extension-feedback"
    profile_code = "EXTENSION"
    form_class = ExtensionRegistrationForm
    reference_field = "course"
