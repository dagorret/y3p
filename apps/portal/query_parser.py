"""Traducción segura de fórmulas a predicados Q del ORM de Django.

La gramática admite condiciones clave:valor unidas por AND, con comillas dobles
para valores con espacios. La lista blanca fija todos los campos y operadores;
los valores se parametrizan por el ORM, sin SQL textual ni evaluación de código.
Los predicados académicos se aplican a una misma vinculación mediante EXISTS,
y los restantes reutilizan el diagnóstico anotado, sin consultas adicionales.
"""

import re
import shlex
from dataclasses import dataclass, field
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone


class QuerySyntaxError(ValidationError):
    """Expresión inválida que las vistas presentan como un error de formulario."""


@dataclass
class QueryCriteria:
    """Separa ámbitos para evitar cruces entre carreras simultáneas del alumno."""

    identity: Q = field(default_factory=Q)
    affiliation: Q = field(default_factory=Q)


HEALTH_VALUES = {'VERDE': 'green', 'AMARILLO': 'yellow', 'NARANJA': 'orange', 'ROJO': 'red'}
ALLOWED_KEYS = {'carrera', 'saga', 'salud', 'dni', 'licencia', 'moodle', 'vence'}


def parse_query(expression, *, now=None):
    """Compila hasta 30 condiciones y 2000 caracteres; no ejecuta consultas."""
    #FQUERY01-8101: Acota la entrada antes del análisis léxico.
    if not isinstance(expression, str) or len(expression) > 2000:
        raise QuerySyntaxError('La fórmula debe ser texto de hasta 2000 caracteres.')
    try:
        tokens = shlex.split(expression)
    except ValueError:
        #FQUERY02-8102: Las comillas incompletas producen un error legible, sin respuesta 500.
        raise QuerySyntaxError('La fórmula contiene comillas sin cerrar.') from None
    criteria = QueryCriteria()
    if not tokens:
        return criteria
    #FQUERY03-8103: Cada operador requiere una condición a ambos lados.
    if len(tokens) > 59 or len(tokens) % 2 == 0 or any(token.upper() != 'AND' for token in tokens[1::2]):
        raise QuerySyntaxError('Use hasta 30 condiciones clave:valor unidas únicamente por AND.')
    now = now or timezone.now()
    for token in tokens[::2]:
        #FQUERY04-8104: Rechaza condiciones incompletas y rutas de campos arbitrarios.
        if token.count(':') != 1:
            raise QuerySyntaxError(f'Condición inválida: «{token}». Use clave:valor.')
        key, value = token.split(':', 1)
        key, value = key.lower(), value.strip()
        if key not in ALLOWED_KEYS:
            #FQUERY05-8105: Ninguna clave externa se usa como nombre de campo ORM.
            raise QuerySyntaxError(f'Clave no permitida: «{key}». Use carrera, saga, salud, dni, licencia, moodle o vence.')
        if not value or len(value) > 150:
            #FQUERY06-8106: Cada condición exige un valor acotado y no vacío.
            raise QuerySyntaxError(f'Indique un valor de hasta 150 caracteres para «{key}».')
        upper = value.upper()
        if key == 'carrera':
            criteria.affiliation &= Q(reference=value) if value.isdecimal() else Q(reference__icontains=value)
        elif key == 'saga':
            #FQUERY07-8107: Los códigos de saga son datos nominales, nunca expresiones ORM.
            if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,49}', upper):
                raise QuerySyntaxError('El estado saga debe ser un código como PRE, ASP, INGRE o ALUMNO.')
            criteria.affiliation &= Q(current_status__code__iexact=upper)
        elif key == 'salud':
            if upper not in HEALTH_VALUES:
                #FQUERY08-8108: El semáforo se traduce desde su catálogo cerrado.
                raise QuerySyntaxError('Salud debe ser VERDE, AMARILLO, NARANJA o ROJO.')
            criteria.identity &= Q(health=HEALTH_VALUES[upper])
        elif key == 'dni':
            if not re.fullmatch(r'[0-9]{1,50}', value):
                #FQUERY09-8109: El DNI admite exclusivamente dígitos como coincidencia o prefijo.
                raise QuerySyntaxError('El DNI debe contener únicamente dígitos.')
            criteria.identity &= Q(identifier__startswith=value)
        elif key in {'licencia', 'moodle'}:
            if upper not in {'SI', 'NO'}:
                #FQUERY10-8110: Solo se aceptan estados booleanos institucionales.
                raise QuerySyntaxError(f'«{key}» admite SI o NO.')
            if key == 'licencia':
                criteria.identity &= Q(_license_known=True, _licensed=upper == 'SI')
            else:
                criteria.identity &= Q(_enrolled=upper == 'SI')
        elif upper == 'VENCIDO':
            criteria.affiliation &= Q(is_active=True, expires_at__lte=now)
        else:
            match = re.fullmatch(r'<([0-9]{1,4})d', value, flags=re.IGNORECASE)
            if not match or not 1 <= int(match[1]) <= 3650:
                #FQUERY11-8111: La ventana temporal es positiva, finita y calculada en el servidor.
                raise QuerySyntaxError('Vence admite vencido o <Nd, con N entre 1 y 3650 días; por ejemplo <30d.')
            criteria.affiliation &= Q(is_active=True, expires_at__gte=now,
                                      expires_at__lt=now + timedelta(days=int(match[1])))
    return criteria
