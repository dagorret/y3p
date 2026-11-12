"""Ficha maestra y endpoints HTMX del área administrativa."""

import json

from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, render
from django.views import View
from django.utils import timezone

from apps.integrations.exceptions import IntegrationError
from apps.people.models import Affiliation, Identity
from apps.people.services import TransitionError
from apps.people.permissions import ADMINISTRATE_STUDENT_CRITICAL, OPERATE_STUDENT_DAILY
from apps.people.support import perform_support, require_permission
from . import admin_services
from .forms import IdentityContactForm, MetadataForm, MoodleExpressForm, TransitionForm, TTLForm
from .permissions import StaffPortalMixin


def ficha_context(identity, *, message='', error=False):
    from .student_grid import student_queryset, describe_health
    diagnosis = student_queryset({}).filter(pk=identity.pk).prefetch_related(None).first()
    if diagnosis:
        describe_health([diagnosis])
    profile = getattr(identity, 'personal_profile', None)
    initial = {'first_name': identity.first_name, 'last_name': identity.last_name}
    initial.update({field: getattr(profile, field, '') for field in ('personal_email', 'phone', 'city', 'address')})
    affiliations = list(identity.affiliations.select_related('profile_type', 'current_status').order_by('-pk'))
    for affiliation in affiliations:
        affiliation.transition_form = TransitionForm(affiliation=affiliation, auto_id=f'id_transition_{affiliation.pk}_%s')
        affiliation.ttl_form = TTLForm(
            initial={'expires_at': timezone.localtime(affiliation.expires_at).date() if affiliation.expires_at else None},
            auto_id=f'id_ttl_{affiliation.pk}_%s',
        )
        affiliation.events = affiliation.history.select_related('changed_by_user', 'from_status', 'to_status').order_by('-occurred_at')[:20]
    return {
        'diagnosis': diagnosis,
        'teams_memberships': identity.teams_memberships.select_related('workspace').prefetch_related('workspace__channels'),
        'identity': identity, 'contact_form': IdentityContactForm(initial=initial),
        'metadata_form': MetadataForm(initial={'operation': 'add'}),
        'metadata_json': json.dumps(identity.metadata, ensure_ascii=False, indent=2),
        'affiliations': affiliations, 'digital_accounts': identity.digital_accounts.all(),
        'moodle_form': MoodleExpressForm(initial={'role_id': 5}), 'message': message, 'error': error,
        'support_log': LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(Identity),
                                              object_id=str(identity.pk)).select_related('user')[:20],
    }


class Identity360View(StaffPortalMixin, View):
    def get(self, request, identity_id):
        #FPORTALADMINVIEWS001-8001: La búsqueda acotada del recurso produce HTTP 404 cuando no existe.
        identity = get_object_or_404(Identity, pk=identity_id)
        template = 'portal/administracion/detail.html' if request.headers.get('HX-Request') == 'true' else 'portal/administracion/person.html'
        #FPORTALADMINVIEWS002-8002: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
        return render(request, template, ficha_context(identity))


class IdentityMutationView(StaffPortalMixin, View):
    required_permission = OPERATE_STUDENT_DAILY
    form_class = None

    def post(self, request, identity_id, **kwargs):
        #FPORTALADMINVIEWS003-8003: La búsqueda acotada del recurso produce HTTP 404 cuando no existe.
        identity = get_object_or_404(Identity, pk=identity_id)
        form_kwargs = {}
        affiliation = None
        if 'affiliation_id' in kwargs:
            require_permission(request.user, ADMINISTRATE_STUDENT_CRITICAL)
            #FPORTALADMINVIEWS004-8004: La búsqueda acotada del recurso produce HTTP 404 cuando no existe.
            affiliation = get_object_or_404(Affiliation, pk=kwargs['affiliation_id'], identity=identity)
            if self.form_class is TransitionForm:
                form_kwargs['affiliation'] = affiliation
        form = self.form_class(request.POST, **form_kwargs) if self.form_class else None
        error = False
        try:
            if form is not None and not form.is_valid():
                #FPORTALADMINVIEWS005-8005: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
                raise ValidationError([f'{name}: {text}' for name, errors in form.errors.items() for text in errors])
            message = self.apply(request, identity, form, affiliation, **kwargs)
        #FPORTALADMINVIEWS006-8006: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
        except (ValidationError, TransitionError) as exc:
            error, message = True, '; '.join(getattr(exc, 'messages', [str(exc)]))
        #FPORTALADMINVIEWS007-8007: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
        except IntegrationError:
            error, message = True, 'El servicio externo no completó la operación. Revise la bitácora antes de reintentar.'
        identity.refresh_from_db()
        context = ficha_context(identity, message=message, error=error)
        # HTMX necesita un 200 para presentar el error de validación en el fragmento.
        if form is not None and not form.is_valid():
            if self.form_class is IdentityContactForm:
                context['contact_form'] = form
            elif self.form_class is MoodleExpressForm:
                context['moodle_form'] = form
        #FPORTALADMINVIEWS008-8008: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
        return render(request, 'portal/administracion/detail.html', context)


class IdentityContactView(IdentityMutationView):
    form_class = IdentityContactForm

    def apply(self, request, identity, form, affiliation, **kwargs):
        admin_services.update_contact(identity.pk, form.cleaned_data, user=request.user)
        return 'Datos de contacto actualizados.'


class IdentityMetadataView(IdentityMutationView):
    form_class = MetadataForm

    def apply(self, request, identity, form, affiliation, **kwargs):
        admin_services.update_metadata(identity.pk, form.cleaned_data, user=request.user)
        return 'Metadatos actualizados.'


class AffiliationTransitionView(IdentityMutationView):
    required_permission = ADMINISTRATE_STUDENT_CRITICAL
    form_class = TransitionForm

    def apply(self, request, identity, form, affiliation, **kwargs):
        admin_services.apply_transition(affiliation, form.cleaned_data, user=request.user)
        return 'Transición aplicada y registrada en el historial.'


class AffiliationTTLView(IdentityMutationView):
    required_permission = ADMINISTRATE_STUDENT_CRITICAL
    form_class = TTLForm

    def apply(self, request, identity, form, affiliation, **kwargs):
        admin_services.update_ttl(affiliation.pk, form.cleaned_data, user=request.user)
        return 'Vigencia actualizada y registrada en el historial.'


class IdentitySupportView(IdentityMutationView):
    def get_required_permission(self):
        return ADMINISTRATE_STUDENT_CRITICAL if self.kwargs['action'] in ('license', 'suspend') else OPERATE_STUDENT_DAILY

    def apply(self, request, identity, form, affiliation, **kwargs):
        result = perform_support(identity.pk, kwargs['action'], user=request.user)
        message = {
            'credentials': 'Clave temporal regenerada y enviada al correo personal.',
            'sessions': 'Sesiones revocadas. Los métodos MFA no fueron eliminados.',
            'license': 'Licencia A1 retirada.' if result.action == 'remove_license' else 'Licencia A1 asignada.',
            'suspend': 'Identidad suspendida.',
        }[kwargs['action']]
        return 'SIMULACIÓN: ' + message if result.simulated else message


class MoodleExpressView(IdentityMutationView):
    form_class = MoodleExpressForm

    def apply(self, request, identity, form, affiliation, **kwargs):
        result = perform_support(identity.pk, kwargs['action'], user=request.user, **form.cleaned_data)
        return 'SIMULACIÓN: operación Moodle validada.' if result.simulated else 'Operación Moodle completada.'


class IdentityPlatformRefreshView(IdentityMutationView):
    """Refresco puntual por POST; la ficha consulta después la pivot con precarga de canales."""

    def apply(self, request, identity, form, affiliation, **kwargs):
        from apps.integrations.catalogs import refresh_identity_platforms
        refresh_identity_platforms(identity, user=request.user)
        return 'Estado de Teams y Moodle actualizado.'
