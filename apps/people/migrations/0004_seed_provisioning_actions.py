"""Configura acciones externas y requisitos de licencia de los estados institucionales."""

from django.db import migrations


def configure_provisioning_actions(apps, schema_editor):
    """Asigna las acciones de entrada definidas en el diseño 01_03, sección 4.

    Actualiza exclusivamente códigos estables de perfil y estado. La migración
    es atómica con su historial; no llama a proveedores y su reverso limpia
    únicamente los valores incorporados por esta operación.
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
    #FSEED09-4009: Solo los estados institucionales definidos aprovisionan o suspenden cuentas.
    for (profile_code, status_code), (action, requires_license) in action_by_state.items():
        Status.objects.using(database).filter(
            profile_type__code=profile_code,
            code=status_code,
        ).update(
            provisioning_action=action,
            requires_m365_license=requires_license,
        )


def clear_provisioning_actions(apps, schema_editor):
    """Restaura los valores sin acción al revertir esta migración de datos."""

    Status = apps.get_model("people", "SagaStatusDefinition")
    database = schema_editor.connection.alias
    #FSEED10-4010: El reverso limpia exclusivamente los valores de esta migración.
    Status.objects.using(database).filter(
        profile_type__code__in=["ALUMNO", "PROFESOR"],
        code__in=["INGRE", "ALUMNO", "GRADUADO", "SI", "NO"],
    ).update(provisioning_action="", requires_m365_license=False)


class Migration(migrations.Migration):
    """Vincula las acciones externas iniciales con el grafo institucional sembrado."""

    dependencies = [("people", "0003_sagastatusdefinition_provisioning_action_and_more")]

    operations = [
        migrations.RunPython(configure_provisioning_actions, clear_provisioning_actions),
    ]
