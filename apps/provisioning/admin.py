"""Read-only Django admin registration for provisioning outbox events.

The admin view is intended for operational diagnosis. It deliberately omits
``payload`` and ``step_results`` because those JSON documents can contain
personal data and provider responses. The model remains writable only through
the transactional producer and worker services, preserving the outbox state
machine and its audit trail.
"""

from django.contrib import admin

from apps.people.models import OutboxEvent


@admin.register(OutboxEvent)
class OutboxEventAdmin(admin.ModelAdmin):
    """Expose safe identifiers and lifecycle metadata without edit operations.

    Every displayed field is read-only. Add and delete are disabled so staff
    cannot bypass event creation in the producer transaction or remove a
    pending/audit record. This class issues read queries only and has no
    transaction side effects.
    """

    list_display = (
        "id",
        "public_id",
        "action",
        "status",
        "attempts",
        "created_at",
        "retry_after",
        "processed_at",
    )
    list_filter = ("action", "status")
    search_fields = ("public_id", "identity__identifier", "affiliation__reference")
    readonly_fields = (
        "id",
        "public_id",
        "action",
        "identity",
        "affiliation",
        "status",
        "attempts",
        "max_attempts",
        "last_error",
        "locked_at",
        "heartbeat_at",
        "retry_after",
        "created_at",
        "processed_at",
    )
    fields = readonly_fields
    ordering = ("-id",)

    def has_add_permission(self, request):
        """Prevent manual event insertion outside the atomic producer service."""

        #FADMIN01-5001: Admin writes would bypass producer transaction invariants.
        return False

    def has_delete_permission(self, request, obj=None):
        """Preserve pending work and the durable operational history."""

        #FADMIN02-5002: Deletion could erase a command before its saga completes.
        return False
