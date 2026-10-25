"""Offer regular and pre-enrollment initial choices to portal registrations."""

from django.db import migrations


def seed_statuses(apps, schema_editor):
    ProfileType = apps.get_model("people", "ProfileTypeDefinition")
    Status = apps.get_model("people", "SagaStatusDefinition")
    database = schema_editor.connection.alias

    for profile_code, profile_name in (("POSGRADO", "Posgrado"), ("EXTENSION", "Extensión")):
        profile = ProfileType.objects.using(database).get(code=profile_code)
        Status.objects.using(database).filter(profile_type=profile, is_initial=True).update(
            is_initial=False
        )
        for status_code, status_name, is_initial in (
            ("REGULAR", "Regular", True),
            ("PRE", "Preinscripto", False),
        ):
            Status.objects.using(database).update_or_create(
                profile_type=profile,
                code=status_code,
                defaults={
                    "name": status_name,
                    "is_initial": is_initial,
                    "is_terminal": False,
                    "default_ttl_days": None,
                    "provisioning_action": "PROVISION",
                    "requires_m365_license": False,
                },
            )


def unseed_statuses(apps, schema_editor):
    ProfileType = apps.get_model("people", "ProfileTypeDefinition")
    Status = apps.get_model("people", "SagaStatusDefinition")
    database = schema_editor.connection.alias
    for profile_code, old_initial in (("POSGRADO", "ALUMNO"), ("EXTENSION", "INSCRIPTO")):
        profile = ProfileType.objects.using(database).get(code=profile_code)
        Status.objects.using(database).filter(
            profile_type=profile, code__in=("REGULAR", "PRE")
        ).delete()
        Status.objects.using(database).filter(
            profile_type=profile, code=old_initial
        ).update(is_initial=True)


class Migration(migrations.Migration):
    dependencies = [("people", "0005_seed_portal_profiles")]

    operations = [migrations.RunPython(seed_statuses, unseed_statuses)]
