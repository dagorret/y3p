"""Operational administration for durable provisioning events."""

import json

from celery import current_app
from django.contrib import admin, messages
from django.db import transaction
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from unfold.decorators import action, display

from apps.people.models import OutboxEvent


@admin.register(OutboxEvent)
class OutboxEventAdmin(ModelAdmin):
    """Inspect and safely requeue durable outbox work."""

    list_display = (
        "id", "public_id", "identity", "action", "status_badge",
        "attempts", "retry_after", "created_at",
    )
    list_filter = ("status", "action", "created_at")
    search_fields = (
        "public_id", "identity__identifier", "identity__institutional_upn", "payload",
    )
    readonly_fields = (
        "id", "public_id", "identity", "affiliation", "action", "created_at",
        "locked_at", "heartbeat_at", "processed_at", "display_payload",
        "display_step_results", "last_error",
    )
    fields = readonly_fields + ("status", "attempts", "max_attempts", "retry_after")
    ordering = ("-id",)
    actions = ("rearm_selected_events", "trigger_dispatch")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "identity", "affiliation", "affiliation__profile_type", "affiliation__current_status"
        )

    @display(description="Estado", ordering="status", label={
        OutboxEvent.Status.SUCCEEDED: "success",
        OutboxEvent.Status.SIMULATED: "purple",
        OutboxEvent.Status.FAILED: "danger",
        OutboxEvent.Status.PROCESSING: "info",
        OutboxEvent.Status.WAITING_SUBTASK: "info",
        OutboxEvent.Status.RETRY_SCHEDULED: "warning",
    })
    def status_badge(self, obj):
        return obj.status

    @display(description="Payload")
    def display_payload(self, obj):
        return format_html("<pre>{}</pre>", json.dumps(obj.payload, ensure_ascii=False, indent=2, sort_keys=True))

    @display(description="Resultados por paso")
    def display_step_results(self, obj):
        return format_html("<pre>{}</pre>", json.dumps(obj.step_results, ensure_ascii=False, indent=2, sort_keys=True))

    @action(description="Rearmar eventos seleccionados", permissions=["change"])
    def rearm_selected_events(self, request, queryset):
        eligible = queryset.filter(status__in=(OutboxEvent.Status.FAILED, OutboxEvent.Status.SIMULATED))
        with transaction.atomic():
            count = eligible.update(
                status=OutboxEvent.Status.PENDING,
                attempts=0,
                last_error="",
                locked_at=None,
                heartbeat_at=None,
                retry_after=None,
                processed_at=None,
            )
        self.message_user(request, f"Se rearmaron {count} evento(s).", messages.SUCCESS)

    @action(description="Encolar despacho del outbox", permissions=["change"])
    def trigger_dispatch(self, request, queryset):
        current_app.send_task("provisioning.dispatch_outbox")
        self.message_user(request, "Se encoló el despacho del outbox.", messages.SUCCESS)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
