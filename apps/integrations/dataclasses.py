"""Objetos de transferencia inmutables para clientes de proveedores externos."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GraphUserResult:
    """Resultado de lectura o creación de usuario Microsoft Graph."""

    id: str
    upn: str
    display_name: str
    created: bool
    simulated: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LicenseResult:
    """Resultado de asignar o retirar una licencia SKU."""

    user_id_or_upn: str
    sku_id: str
    assigned: bool
    simulated: bool = False


@dataclass(frozen=True)
class GraphAccountResult:
    """Confirmed enabled state for an Entra user, with simulation marker."""

    user_identifier: str
    enabled: bool
    simulated: bool = False


@dataclass(frozen=True)
class PasswordSetResult:
    """Confirms a temporary password mutation without retaining the secret."""

    user_identifier: str
    simulated: bool = False


@dataclass(frozen=True)
class MoodleUserResult:
    """Resultado tipado de usuario Moodle."""

    id: int
    username: str
    email: str
    created: bool
    simulated: bool = False


@dataclass(frozen=True)
class MoodleEnrollmentResult:
    """Resultado de matrícula o baja de matrícula en un curso."""

    user_id: int
    course_id: int
    role_id: int
    success: bool
    simulated: bool = False


@dataclass(frozen=True)
class MoodleGroupResult:
    """Resultado de agregar o quitar un usuario de un grupo Moodle."""

    group_id: int
    course_id: int
    user_id: int
    action: str
    success: bool
    simulated: bool = False


@dataclass(frozen=True)
class SialStudentDTO:
    """Datos normalizados de un estudiante entregados por UTI / SIAL."""

    dni: str
    first_name: str
    last_name: str
    personal_email: str
    career_code: str
    modality: str
    condition: str


@dataclass(frozen=True)
class MailDeliveryResult:
    """Resultado de entrega SMTP, incluyendo simulación y errores capturados."""

    recipient: str
    subject: str
    success: bool
    message_id: str | None = None
    simulated: bool = False
    error_message: str | None = None
