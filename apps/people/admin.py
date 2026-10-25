"""Administrative views for people and superuser governed configuration."""

from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import (
    Affiliation,
    AffiliationStatusHistory,
    CrossProfileConstraint,
    Identity,
    OutboxEvent,
    ProfileTypeDefinition,
    SagaStatusDefinition,
    SagaTransitionRule,
)


def is_superuser(request):
    """Reserve state machine configuration for superusers."""

    return request.user.is_active and request.user.is_staff and request.user.is_superuser


class AffiliationInline(TabularInline):
    model = Affiliation
    fields = ("profile_type", "current_status", "reference", "modality", "is_active", "start_date")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Identity)
class IdentityAdmin(ModelAdmin):
    list_display = ("identifier", "first_name", "last_name", "institutional_upn")
    search_fields = ("identifier", "first_name", "last_name", "institutional_upn")
    list_select_related = ()
    inlines = (AffiliationInline,)
    readonly_fields = ("category", "identifier", "first_name", "last_name", "institutional_upn", "created_at", "updated_at")

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
    """Enforce superuser access at every admin endpoint, not only navigation."""

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


admin.site.register(ProfileTypeDefinition, SuperuserConfigurationAdmin)
admin.site.register(SagaStatusDefinition, SuperuserConfigurationAdmin)
admin.site.register(SagaTransitionRule, SuperuserConfigurationAdmin)
admin.site.register(CrossProfileConstraint, SuperuserConfigurationAdmin)
