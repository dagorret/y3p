"""Administración de personas y configuración reservada a superusuarios."""

import json

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action

from apps.integrations.exceptions import IntegrationError
from .support import SUPPORT_ACTIONS, perform_support

from .models import (
    Affiliation,
    AffiliationStatusHistory,
    CrossProfileConstraint,
    DigitalAccount,
    Identity,
    OutboxEvent,
    PersonalProfile,
    ProfileTypeDefinition,
    SagaStatusDefinition,
    SagaTransitionRule,
)


def is_superuser(request):
    """Reserva la configuración de la máquina de estados a superusuarios."""

    return request.user.is_active and request.user.is_staff and request.user.is_superuser


class AffiliationInline(TabularInline):
    model = Affiliation
    fields = ("profile_type", "current_status", "reference", "modality", "is_active", "start_date")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DigitalAccountInline(TabularInline):
    """Audita las cuentas asociadas sin habilitar cambios de aprovisionamiento."""

    model = DigitalAccount
    fields = (
        "platform", "account_identifier", "is_active", "content_hash", "observations",
        "created_at", "updated_at",
    )
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PersonalProfileInline(TabularInline):
    model = PersonalProfile
    fields = ('personal_email', 'phone', 'city', 'address', 'birth_date')
    extra = 0
    can_delete = False


@admin.register(Identity)
class IdentityAdmin(ModelAdmin):
    list_display = ("identifier", "first_name", "last_name", "institutional_upn")
    search_fields = ("identifier", "first_name", "last_name", "institutional_upn")
    list_select_related = ()
    inlines = (PersonalProfileInline, AffiliationInline, DigitalAccountInline)
    readonly_fields = ("category", "identifier", "first_name", "last_name", "institutional_upn", "created_at", "updated_at", 'metadata_display', 'ficha_link')
    actions = ('action_reenviar_credenciales', 'action_revocar_sesiones_mfa',
               'action_conmutar_licencia_a1', 'action_suspender_identidad')
    actions_detail = ('credentials_detail', 'sessions_detail', 'license_detail', 'suspend_detail')

    @admin.display(description='Metadatos JSON')
    def metadata_display(self, obj):
        return format_html('<pre>{}</pre>', json.dumps(obj.metadata, ensure_ascii=False, indent=2))

    @admin.display(description='Ficha maestra')
    def ficha_link(self, obj):
        from django.urls import reverse
        return format_html('<a href="{}">Abrir Ficha 360°</a>', reverse('portal:identity360', args=[obj.pk]))

    def _support_action(self, request, queryset, operation, action_name='', detail=False):
        if not self.has_change_permission(request):
            #FPEOPLEADMIN001-8001: Propaga el fallo original sin convertirlo en éxito.
            raise PermissionDenied
        if request.method != 'POST' or request.POST.get('confirm_support') != 'yes':
            #FPEOPLEADMIN002-8002: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
            return TemplateResponse(request, 'admin/people/support_confirmation.html', {
                **self.admin_site.each_context(request), 'title': SUPPORT_ACTIONS[operation],
                'objects': queryset, 'action_name': action_name, 'opts': self.model._meta,
            })
        for identity in queryset:
            try:
                result = perform_support(identity.pk, operation, user=request.user)
                self.message_user(request, f"{identity.identifier}: {'SIMULACIÓN — ' if result.simulated else ''}{SUPPORT_ACTIONS[operation]} completada.", messages.SUCCESS)
            #FPEOPLEADMIN003-8003: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
            except (IntegrationError, ValidationError) as error:
                self.message_user(request, f'{identity.identifier}: operación no completada ({type(error).__name__}). Consulte la bitácora.', messages.ERROR)
        if detail:
            #FPEOPLEADMIN004-8004: Respuesta HTTP 302: mantiene el contrato de página, fragmento o redirección.
            return redirect('admin:people_identity_change', queryset[0].pk)

    @admin.action(description='Reenviar credenciales', permissions=['change'])
    def action_reenviar_credenciales(self, request, queryset):
        return self._support_action(request, queryset, 'credentials', 'action_reenviar_credenciales')

    @admin.action(description='Revocar sesiones Azure (no elimina MFA)', permissions=['change'])
    def action_revocar_sesiones_mfa(self, request, queryset):
        return self._support_action(request, queryset, 'sessions', 'action_revocar_sesiones_mfa')

    @admin.action(description='Conmutar licencia A1', permissions=['change'])
    def action_conmutar_licencia_a1(self, request, queryset):
        return self._support_action(request, queryset, 'license', 'action_conmutar_licencia_a1')

    @admin.action(description='Suspender identidad', permissions=['change'])
    def action_suspender_identidad(self, request, queryset):
        return self._support_action(request, queryset, 'suspend', 'action_suspender_identidad')

    def _detail_support(self, request, object_id, operation):
        #FPEOPLEADMIN005-8005: La búsqueda acotada del recurso produce HTTP 404 cuando no existe.
        identity = get_object_or_404(Identity, pk=object_id)
        return self._support_action(request, [identity], operation, detail=True)

    @action(description='Reenviar credenciales', permissions=['change'])
    def credentials_detail(self, request, object_id):
        return self._detail_support(request, object_id, 'credentials')

    @action(description='Revocar sesiones', permissions=['change'])
    def sessions_detail(self, request, object_id):
        return self._detail_support(request, object_id, 'sessions')

    @action(description='Conmutar A1', permissions=['change'])
    def license_detail(self, request, object_id):
        return self._detail_support(request, object_id, 'license')

    @action(description='Suspender identidad', permissions=['change'])
    def suspend_detail(self, request, object_id):
        return self._detail_support(request, object_id, 'suspend')

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("affiliations__profile_type", "affiliations__current_status")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class AffiliationHistoryInline(TabularInline):
    model = AffiliationStatusHistory
    fields = ("from_status", "to_status", "changed_by", "reason", "occurred_at")
    readonly_fields = fields
    extra = 0
    can_delete = False
    ordering = ("-occurred_at",)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class OutboxEventInline(TabularInline):
    model = OutboxEvent
    fields = ("public_id", "action", "status", "attempts", "created_at", "processed_at")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Affiliation)
class AffiliationAdmin(ModelAdmin):
    list_display = ("identity", "profile_type", "current_status", "reference", "is_active", "start_date")
    list_filter = ("profile_type", "current_status", "is_active", "modality")
    search_fields = ("identity__identifier", "identity__first_name", "identity__last_name", "reference")
    readonly_fields = ("identity", "profile_type", "current_status", "reference", "modality", "start_date", "expires_at", "is_active")
    inlines = (AffiliationHistoryInline, OutboxEventInline)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("identity", "profile_type", "current_status")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SuperuserConfigurationAdmin(ModelAdmin):
    """Exige superusuario en cada ruta del administrador, incluida la solicitud directa."""

    def has_module_permission(self, request):
        return is_superuser(request)

    def has_view_permission(self, request, obj=None):
        return is_superuser(request)

    def has_add_permission(self, request):
        return is_superuser(request)

    def has_change_permission(self, request, obj=None):
        return is_superuser(request)

    def has_delete_permission(self, request, obj=None):
        return is_superuser(request)


@admin.register(SagaStatusDefinition)
class SagaStatusDefinitionAdmin(SuperuserConfigurationAdmin):
    fieldsets = (
        ("Estado y ciclo de vida", {"fields": (
            "profile_type", "code", "name", "is_initial", "is_terminal", "default_ttl_days",
        )}),
        ("Plataforma y aprovisionamiento", {"fields": (
            "target_platform", "requires_collaboration_account",
            "requires_collaboration_license", "provisioning_action",
        )}),
    )


admin.site.register(ProfileTypeDefinition, SuperuserConfigurationAdmin)
admin.site.register(SagaTransitionRule, SuperuserConfigurationAdmin)
admin.site.register(CrossProfileConstraint, SuperuserConfigurationAdmin)
