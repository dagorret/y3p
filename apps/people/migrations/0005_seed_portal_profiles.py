"""Seed profile types used by postgraduate and extension registrations."""

from django.db import migrations


def seed_portal_profiles(apps, schema_editor):
    """Create the canonical initial states that emit durable provisioning work."""

    ProfileType = apps.get_model("people", "ProfileTypeDefinition")
    Status = apps.get_model("people", "SagaStatusDefinition")
    database = schema_editor.connection.alias
    definitions = (
        ("POSGRADO", "Posgrado", "ALUMNO", "Alumno de posgrado"),
        ("EXTENSION", "Extensión", "INSCRIPTO", "Inscripto"),
    )
    for code, name, status_code, status_name in definitions:
        profile, _ = ProfileType.objects.using(database).update_or_create(
            code=code,
            defaults={
                "name": name,
                "is_organizational": False,
                "allows_multiple_instances": True,
            },
        )
        Status.objects.using(database).update_or_create(
            profile_type=profile,
            code=status_code,
            defaults={
                "name": status_name,
                "is_initial": True,
                "is_terminal": False,
                "default_ttl_days": None,
                "provisioning_action": "PROVISION",
                "requires_m365_license": False,
            },
        )


def unseed_portal_profiles(apps, schema_editor):
    """Remove only the portal-specific seed rows on migration rollback."""

    ProfileType = apps.get_model("people", "ProfileTypeDefinition")
    Status = apps.get_model("people", "SagaStatusDefinition")
    database = schema_editor.connection.alias
    for profile_code, status_code in (("POSGRADO", "ALUMNO"), ("EXTENSION", "INSCRIPTO")):
        Status.objects.using(database).filter(
            profile_type__code=profile_code,
            code=status_code,
        ).delete()
    ProfileType.objects.using(database).filter(code__in=("POSGRADO", "EXTENSION")).delete()


class Migration(migrations.Migration):
    """Make required portal registration types available in every environment."""

    dependencies = [("people", "0004_seed_provisioning_actions")]

    operations = [
        migrations.RunPython(seed_portal_profiles, unseed_portal_profiles),
    ]
