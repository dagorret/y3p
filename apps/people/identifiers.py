"""UUIDv8 institucionales deterministas, sin dependencias de Django ni de BD.

Formato: ``fce00000-0000-8YYY-90OT-DDDDDDDDDDDD``. El año ocupa 12 bits,
origen y tipo 4 bits cada uno y el DNI decimal se codifica en 48 bits.
Los ceros iniciales del DNI no son significativos: se devuelve normalizado.
"""

import hashlib
import json
import re
import uuid


def calculate_content_hash(data: dict) -> str:
    """Calcula SHA-256 del JSON ordenado con valores de texto normalizados.

    Aplica strip a valores de texto, incluso en diccionarios y listas anidados,
    sin modificar el argumento ni las claves. Conserva tipos y orden de listas.
    """

    if not isinstance(data, dict):
        raise TypeError("data debe ser un diccionario.")

    def sanitize(value):
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [sanitize(item) for item in value]
        return value

    payload = json.dumps(sanitize(data), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_integer(value: int, name: str, maximum: int) -> None:
    """Rechaza tipos no enteros y valores que desbordarían su campo."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} debe ser un entero.")
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} debe estar entre 0 y {maximum}.")


def generate_institutional_uuid(
    dni: str, year: int, origin_code: int = 1, profile_type_code: int = 1
) -> uuid.UUID:
    """Empaqueta un DNI decimal (cadena o entero) en el layout institucional.

    Se permiten espacios exteriores en las cadenas. No se aceptan signos,
    separadores, flotantes ni booleanos. Los campos deben caber sin truncarse.
    El nibble de versión es 8 y el byte alto del cuarto grupo es 0x90,
    que fija la variante RFC con bits iniciales 10 y la reserva canónica.
    """

    if isinstance(dni, bool) or not isinstance(dni, (str, int)):
        raise TypeError("dni debe ser una cadena decimal o un entero.")
    decimal_dni = str(dni).strip()
    if re.fullmatch(r"[0-9]+", decimal_dni) is None:
        raise ValueError("dni debe contener únicamente dígitos decimales.")
    dni_number = int(decimal_dni)
    _validate_integer(dni_number, "dni", (1 << 48) - 1)
    _validate_integer(year, "year", 0xFFF)
    _validate_integer(origin_code, "origin_code", 0xF)
    _validate_integer(profile_type_code, "profile_type_code", 0xF)

    return uuid.UUID(
        f"fce00000-0000-8{year:03x}-90{origin_code:x}{profile_type_code:x}"
        f"-{dni_number:012x}"
    )


def decode_institutional_uuid(u: uuid.UUID) -> dict:
    """Decodifica únicamente UUIDs que respetan el layout canónico completo."""

    if not isinstance(u, uuid.UUID):
        raise TypeError("u debe ser un uuid.UUID.")
    if (
        u.time_low != 0xFCE00000
        or u.time_mid != 0
        or u.version != 8
        or u.variant != uuid.RFC_4122
        or u.clock_seq_hi_variant != 0x90
    ):
        raise ValueError("El UUID no cumple el formato institucional canónico.")

    return {
        "prefix": "fce",
        "year": u.time_hi_version & 0xFFF,
        "origin_code": (u.clock_seq_low >> 4) & 0xF,
        "profile_type_code": u.clock_seq_low & 0xF,
        "dni": str(u.node),
    }
