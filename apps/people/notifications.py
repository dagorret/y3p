"""Plantillas institucionales seguras para el Outbox transaccional.

Solo se sustituyen variables nominales autorizadas; no se accede a atributos
ni se ejecutan expresiones. El mensaje final se persiste con el lote para que
los reintentos conserven el contenido confirmado por el operador.
"""

from string import Formatter

from django.core.exceptions import ValidationError
from django.core.validators import validate_email


def validate_notification(options):
    """Valida límites y variables antes de registrar cualquier evento del lote."""
    subject = options.get('subject', '').strip()
    body = options.get('body', '').strip()
    #FNOTIFY01-8001: Rechaza mensajes vacíos, cabeceras inyectadas o tamaños excesivos.
    if not subject or not body or len(subject) > 200 or len(body) > 10000 or '\n' in subject or '\r' in subject:
        raise ValidationError('Indique asunto (hasta 200 caracteres, sin saltos) y cuerpo (hasta 10000 caracteres).')
    try:
        for template in (subject, body):
            for _, field, spec, conversion in Formatter().parse(template):
                #FNOTIFY02-8002: Solo se admiten sustituciones simples del catálogo institucional.
                if field is not None and (field not in {'full_name', 'upn', 'document_number'} or spec or conversion):
                    raise ValueError
    except ValueError:
        #FNOTIFY03-8003: Traduce errores de sintaxis a un mensaje institucional.
        raise ValidationError('Variables inválidas. Use {full_name}, {upn} o {document_number}.') from None
    return {'subject': subject, 'body': body}


def render_notification(identity, options):
    """Materializa destinatario y texto sin consultar proveedores externos."""
    #FNOTIFY04-8004: El UPN institucional debe ser un destinatario válido.
    validate_email(identity.institutional_upn or '')
    variables = {'full_name': f'{identity.first_name} {identity.last_name}'.strip(),
                 'upn': identity.institutional_upn, 'document_number': identity.identifier}
    rendered = {key: value.format_map(variables) for key, value in options.items()}
    #FNOTIFY05-8005: También valida el resultado para evitar cabeceras nominales inválidas.
    if len(rendered['subject']) > 200 or '\n' in rendered['subject'] or '\r' in rendered['subject']:
        raise ValidationError('El asunto resultante supera el límite o contiene saltos de línea.')
    return {**rendered, 'recipient': identity.institutional_upn}
