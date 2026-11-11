"""Cohorte sintética e ingesta atómica por registro, sin llamadas a APIs."""

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.people.models import Affiliation, ConfigurationError, Identity, PersonalProfile, ProfileTypeDefinition
from apps.people.services import create_affiliation
from .models import MockSialRecord


def require_local_mocks():
    if not settings.ENABLE_LOCAL_MOCKS:
        raise ConfigurationError('Esta operación requiere ENABLE_LOCAL_MOCKS=True.')


@transaction.atomic
def seed_mock_sial():
    """Actualiza solo la cohorte reservada 90000000–90000149, sin borrar otros datos."""

    require_local_mocks()
    names = ('Ana', 'Bruno', 'Carla', 'Diego', 'Elena', 'Facundo')
    surnames = ('Pérez', 'Gómez', 'López', 'Torres', 'Suárez')
    created = 0
    for index in range(150):
        if index < 100:
            career = ('2', '3', '4')[index % 3]
            commission = 'COMISION 1' if index < 50 else 'COMISION 2'
            modality = 'P'
        else:
            career = '7' if index < 125 else '8'
            commission = None
            modality = 'P' if career == '7' else 'D'
        dni = str(90000000 + index)
        domain = 'gmail.com' if index % 2 == 0 else 'hotmail.com'
        values = {
            'first_name': names[index % len(names)],
            'last_name': surnames[index % len(surnames)],
            'personal_email': f'lucy.prueba{dni}@{domain}',
            'condition': 'INGRESANTE', 'career_code': career,
            'modality': modality, 'commission': commission,
        }
        MockSialRecord(dni=dni, **values).full_clean()
        _, new = MockSialRecord.objects.update_or_create(dni=dni, defaults=values)
        created += int(new)
    return created


@transaction.atomic
def ingest_mock_record(record):
    """Confirma identidad, vinculación, historial y payload completo juntos."""

    require_local_mocks()
    dni = record.dni.strip()
    if re.fullmatch(r'[0-9]{7,8}', dni) is None:
        raise ValidationError('DNI del padrón inválido.')
    modality_code = record.modality.strip().upper()
    modalities = {'1': 'PRESENCIAL', 'P': 'PRESENCIAL', '2': 'DISTANCIA', 'D': 'DISTANCIA'}
    if modality_code not in modalities:
        raise ValidationError('Modalidad del padrón no reconocida.')
    record.full_clean()
    profile = ProfileTypeDefinition.objects.get(code='ALUMNO')
    status = profile.statuses.get(code='INGRE')
    if status.provisioning_action != 'PROVISION' or not status.requires_collaboration_license:
        raise ConfigurationError('ALUMNO/INGRE debe aprovisionar y requerir licencia.')
    upn = f'a{dni}@eco.unrc.edu.ar'
    identity, _ = Identity.objects.select_for_update().get_or_create(
        identifier=dni,
        defaults={
            'category': Identity.Category.FISICA, 'first_name': record.first_name.strip(),
            'last_name': record.last_name.strip(), 'institutional_upn': upn,
        },
    )
    if identity.category != Identity.Category.FISICA or identity.institutional_upn not in (None, '', upn):
        raise ValidationError('La identidad existente no coincide con el padrón de ingreso.')
    if not identity.institutional_upn:
        identity.institutional_upn = upn
        identity.save(update_fields=['institutional_upn', 'updated_at'])
    PersonalProfile.objects.get_or_create(
        identity=identity, defaults={'personal_email': record.personal_email.strip()},
    )
    reference = record.career_code.strip()
    if not reference:
        raise ValidationError('El padrón debe indicar carrera.')
    if Affiliation.objects.filter(identity=identity, profile_type=profile, reference=reference).exists():
        return False
    affiliation = create_affiliation(
        identity, profile, initial_status_code='INGRE', changed_by='mock:sial_ingreso',
        reason='Ingesta de padrón SIAL local', reference=reference,
        modality=modalities[modality_code], commission=(record.commission or '').strip(),
    )
    # Conserva el código de modalidad de entrada para resolver reglas P/D o 1/2.
    event = affiliation.outbox_events.get()
    event.payload = {**event.payload, 'modality': modality_code}
    event.save(update_fields=['payload'])
    return True


def sync_sial_ingreso():
    """Ingiere los ingresantes locales sin duplicar vinculaciones existentes.

    Cada fila es una transacción: un fallo revierte esa fila, conservando las
    anteriores. El callback del dominio puede notificar Celery tras el commit.
    """

    require_local_mocks()
    result = {'processed': 0, 'created': 0, 'existing': 0}
    for record in MockSialRecord.objects.filter(condition__iexact='INGRESANTE').order_by('pk').iterator():
        new = ingest_mock_record(record)
        result['processed'] += 1
        result['created' if new else 'existing'] += 1
    return result
