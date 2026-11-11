"""Edición administrativa con validación, bloqueo y trazabilidad."""

import json
from datetime import datetime, time

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.people.models import Affiliation, AffiliationStatusHistory, Identity, PersonalProfile
from apps.people.services import transition_affiliation
from apps.people.support import audit, require_permission
from apps.people.permissions import ADMINISTRATE_STUDENT_CRITICAL, OPERATE_STUDENT_DAILY


@transaction.atomic
def update_contact(identity_id, data, *, user):
    require_permission(user, OPERATE_STUDENT_DAILY)
    identity = Identity.objects.select_for_update().get(pk=identity_id)
    identity.first_name, identity.last_name = data['first_name'], data['last_name']
    identity.full_clean()
    identity.save(update_fields=['first_name', 'last_name', 'updated_at'])
    profile, _ = PersonalProfile.objects.get_or_create(identity=identity)
    for key in ('personal_email', 'phone', 'city', 'address'):
        setattr(profile, key, data[key])
    profile.full_clean()
    profile.save()
    audit(identity, user, 'contact', status='SUCCEEDED')


@transaction.atomic
def update_metadata(identity_id, data, *, user):
    require_permission(user, OPERATE_STUDENT_DAILY)
    identity = Identity.objects.select_for_update().get(pk=identity_id)
    if not isinstance(identity.metadata, dict):
        #FPORTALADMINSERVICES001-8001: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('Los metadatos existentes no son un objeto JSON.')
    metadata = dict(identity.metadata)
    if data['operation'] == 'remove':
        metadata.pop(data['key'], None)
    else:
        try:
            value = json.loads(data['value'])
        #FPORTALADMINSERVICES002-8002: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
        except (ValueError, TypeError):
            value = data['value']
        metadata[data['key']] = value
    identity.metadata = metadata
    identity.save(update_fields=['metadata', 'updated_at'])
    audit(identity, user, 'metadata', status='SUCCEEDED', operation=data['operation'])


def apply_transition(affiliation, data, *, user):
    require_permission(user, ADMINISTRATE_STUDENT_CRITICAL)
    return transition_affiliation(
        affiliation.pk, data['rule'].to_status.code, user=user,
        changed_by=f'operador:{user.get_username()}', reason=data['reason'],
        expected_from_code=data['expected_from_code'],
    )


@transaction.atomic
def update_ttl(affiliation_id, data, *, user):
    require_permission(user, ADMINISTRATE_STUDENT_CRITICAL)
    affiliation = Affiliation.objects.select_for_update().select_related('current_status').get(pk=affiliation_id)
    if not affiliation.is_active or affiliation.current_status.is_terminal:
        #FPORTALADMINSERVICES003-8003: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
        raise ValidationError('Solo puede modificar la vigencia de vinculaciones activas no terminales.')
    affiliation.expires_at = timezone.make_aware(datetime.combine(data['expires_at'], time.max))
    affiliation.save(update_fields=['expires_at'])
    AffiliationStatusHistory.objects.create(
        affiliation=affiliation, from_status=affiliation.current_status, to_status=affiliation.current_status,
        changed_by=f'operador:{user.get_username()}', changed_by_user=user,
        reason=f"TTL {data['expires_at'].isoformat()}: {data['reason']}"[:255],
    )
