"""Configure event actions and shared license requirements for FCE states."""

from django.db import migrations


def configure_provisioning_actions(apps, schema_editor):
    """Set state-entry intents from canonical 01_03 section 4.

    Existing status rows are updated only by stable profile and status codes.
    The migration is atomic with the schema history; no external integrations
    are called and rollback clears only these newly introduced settings.
    """

    Status = apps.get_model("people", "SagaStatusDefinition")
    database = schema_editor.connection.alias
    action_by_state = {
        ("ALUMNO", "INGRE"): ("PROVISION", True),
        ("ALUMNO", "ALUMNO"): ("PROVISION", True),
        ("ALUMNO", "GRADUADO"): ("SUSPEND", False),
        ("PROFESOR", "SI"): ("PROVISION", True),
        ("PROFESOR", "NO"): ("SUSPEND", False),
    }
    #FSEED09-4009: Only the canonical states provision or suspend accounts.
    for (profile_code, status_code), (action, requires_license) in action_by_state.items():
        Status.objects.using(database).filter(
            profile_type__code=profile_code,
            code=status_code,
        ).update(
            provisioning_action=action,
            requires_m365_license=requires_license,
        )


def clear_provisioning_actions(apps, schema_editor):
    """Restore the prior no-action values when reversing this data migration."""

    Status = apps.get_model("people", "SagaStatusDefinition")
    database = schema_editor.connection.alias
    #FSEED10-4010: Reversal removes only values introduced by this migration.
    Status.objects.using(database).filter(
        profile_type__code__in=["ALUMNO", "PROFESOR"],
        code__in=["INGRE", "ALUMNO", "GRADUADO", "SI", "NO"],
    ).update(provisioning_action="", requires_m365_license=False)


class Migration(migrations.Migration):
    """Attach canonical integration intent to the seeded institutional graph."""

    dependencies = [("people", "0003_sagastatusdefinition_provisioning_action_and_more")]

    operations = [
        migrations.RunPython(configure_provisioning_actions, clear_provisioning_actions),
    ]
