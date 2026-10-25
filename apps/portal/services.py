"""Transactional helpers that connect public forms to people domain services."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.people.models import Identity, PersonalProfile, ProfileTypeDefinition
from apps.people.services import create_affiliation


@transaction.atomic
def register_affiliation(*, profile_code, reference, form_data):
    """Reuse or create an identity and create its affiliation via domain service.

    Identity creation, personal contact, affiliation, history, and any outbox
    event share one PostgreSQL transaction. A unique-key race on identifier is
    recovered by re-reading the identity created by the concurrent request.
    """

    try:
        identity = Identity.objects.select_for_update().get(
            identifier=form_data["dni"]
        )
        if identity.category != Identity.Category.FISICA:
            raise ValidationError({"dni": "El identificador ya pertenece a una entidad organizacional."})
    except Identity.DoesNotExist:
        try:
            # A savepoint keeps the outer registration transaction usable if
            # another request wins the same unique identifier concurrently.
            with transaction.atomic():
                identity = Identity.objects.create(
                    category=Identity.Category.FISICA,
                    identifier=form_data["dni"],
                    first_name=form_data["first_name"],
                    last_name=form_data["last_name"],
                )
        except IntegrityError:
            identity = Identity.objects.select_for_update().get(
                identifier=form_data["dni"]
            )
            if identity.category != Identity.Category.FISICA:
                raise ValidationError({"dni": "El identificador ya pertenece a una entidad organizacional."})

    profile, _ = PersonalProfile.objects.get_or_create(
        identity=identity,
        defaults={"personal_email": form_data["personal_email"]},
    )
    if not profile.personal_email:
        profile.personal_email = form_data["personal_email"]
        profile.save(update_fields=["personal_email"])

    try:
        profile_type = ProfileTypeDefinition.objects.get(code=profile_code)
    except ProfileTypeDefinition.DoesNotExist:
        raise ValidationError("El tipo de vinculación no está configurado.") from None

    affiliation = create_affiliation(
        identity,
        profile_type,
        changed_by="portal:public_registration",
        reason=f"Inscripción a {reference}",
        reference=reference,
    )
    return identity, affiliation
