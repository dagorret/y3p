"""Servicios transaccionales que conectan los formularios con el dominio de personas."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.people.models import (
    ConfigurationError,
    Identity,
    PersonalProfile,
    ProfileTypeDefinition,
)
from apps.people.services import create_affiliation


@transaction.atomic
def register_affiliation(*, profile_code, reference, form_data):
    """Reutiliza o crea una identidad y registra su vinculación mediante el dominio.

    Identidad, contacto, vinculación, historial y evento externo comparten una
    transacción PostgreSQL. Si otra solicitud gana la creación del mismo
    identificador, un punto de recuperación permite releer esa identidad
    sin invalidar la transacción exterior.
    """

    try:
        identity = Identity.objects.select_for_update().get(
            identifier=form_data["dni"]
        )
        if identity.category != Identity.Category.FISICA:
            #FPORTALSERVICES001-8001: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
            raise ValidationError({"dni": "El identificador ya pertenece a una entidad organizacional."})
    #FPORTALSERVICES002-8002: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
    except Identity.DoesNotExist:
        try:
            # Un punto de recuperación conserva la transacción exterior si otra solicitud
            # crea primero el mismo identificador; después se relee la identidad existente.
            with transaction.atomic():
                identity = Identity.objects.create(
                    category=Identity.Category.FISICA,
                    identifier=form_data["dni"],
                    first_name=form_data["first_name"],
                    last_name=form_data["last_name"],
                )
        #FPORTALSERVICES003-8003: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
        except IntegrityError:
            identity = Identity.objects.select_for_update().get(
                identifier=form_data["dni"]
            )
            if identity.category != Identity.Category.FISICA:
                #FPORTALSERVICES004-8004: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
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
    #FPORTALSERVICES005-8005: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
    except ProfileTypeDefinition.DoesNotExist:
        #FPORTALSERVICES006-8006: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError("El tipo de vinculación no está configurado.") from None

    try:
        affiliation = create_affiliation(
            identity,
            profile_type,
            changed_by="portal:public_registration",
            reason=f"Inscripción a {reference}",
            initial_status_code=form_data.get("initial_status_code"),
            reference=reference,
        )
    #FPORTALSERVICES007-8007: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
    except ConfigurationError as error:
        #FPORTALSERVICES008-8008: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError(str(error)) from error
    return identity, affiliation
