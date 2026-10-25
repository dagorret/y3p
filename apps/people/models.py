"""Módulo de Identidades, Vinculaciones y Máquina de Estados Configurable.

Sistema Y3P - FCE / UNRC. La capa de metamodelo define tipos, estados y reglas;
la capa operativa conserva identidades y vinculaciones; la capa de auditoría
registra los cambios como filas append-only. Las validaciones entre filas viven
en ``clean()`` y las restricciones locales se delegan a PostgreSQL.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ConfigurationError(Exception):
    """Indica que falta una pieza obligatoria del metamodelo persistido.

    Esta excepción se produce al consultar la configuración; no escribe datos
    ni abre por sí sola una transacción. El servicio que la detecta debe dejar
    que la operación atómica falle completa.
    """

    #FCFG01-0001: Error fatal de configuración del metamodelo.


# ======================================================================
# CAPA 1: METAMODELO (Configuración gobernada por datos)
# ======================================================================


class ProfileTypeDefinition(models.Model):
    """Define una clase institucional de vinculación, como ALUMNO o PROFESOR.

    ``code`` es la clave estable usada por los servicios. Las banderas expresan
    si la categoría representa una unidad organizacional y si una identidad
    puede mantener más de una afiliación activa de ese tipo.
    """

    code = models.CharField(max_length=30, unique=True, db_index=True)
    name = models.CharField(max_length=100)
    is_organizational = models.BooleanField(
        default=False,
        help_text="True si aplica a cátedras/secretarías/áreas, False para personas físicas.",
    )
    allows_multiple_instances = models.BooleanField(
        default=False,
        help_text="True si permite instancias concurrentes activas (ej: carreras simultáneas).",
    )

    class Meta:
        """Orden y etiquetas de administración del catálogo de perfiles."""

        ordering = ["code"]
        verbose_name = "Definición de Tipo de Perfil"
        verbose_name_plural = "Definiciones de Tipos de Perfil"

    def __str__(self):
        """Devuelve el identificador estable para logs y administración."""

        return self.code

    @property
    def initial_status(self):
        """Devuelve el estado inicial único configurado para este perfil.

        La restricción parcial de PostgreSQL impide más de un inicial, pero no
        puede exigir que exista al menos uno; esta propiedad protege ese
        invariante al crear afiliaciones. Solo consulta la base de datos.
        """

        status = self.statuses.filter(is_initial=True).first()
        if status is None:
            #FCFG02-0002: El tipo carece del estado inicial requerido.
            raise ConfigurationError(
                f"El tipo de perfil {self.code} no tiene un estado inicial definido (is_initial=True)."
            )
        return status


class SagaStatusDefinition(models.Model):
    """Estado permitido dentro del ciclo de vida de un tipo de perfil.

    El TTL se usa al entrar al estado para calcular ``Affiliation.expires_at``;
    un estado terminal indica que la afiliación debe quedar inactiva.
    """

    profile_type = models.ForeignKey(
        ProfileTypeDefinition, on_delete=models.PROTECT, related_name="statuses"
    )
    code = models.CharField(max_length=30, db_index=True)
    name = models.CharField(max_length=100)
    is_initial = models.BooleanField(default=False)
    is_terminal = models.BooleanField(
        default=False,
        help_text="Un estado terminal desactiva la vinculación (is_active=False).",
    )
    default_ttl_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Días de vigencia máxima en este estado antes de disparar vencimiento por TTL.",
    )

    class ProvisioningAction(models.TextChoices):
        """Intenciones externas emitidas al ingresar a un estado."""

        PROVISION = "PROVISION", "Aprovisionar"
        SUSPEND = "SUSPEND", "Suspender"
        TERMINATE = "TERMINATE", "Dar de baja"

    provisioning_action = models.CharField(
        max_length=16,
        choices=ProvisioningAction.choices,
        blank=True,
        help_text="Acción de aprovisionamiento emitida al entrar a este estado.",
    )
    requires_m365_license = models.BooleanField(
        default=False,
        help_text="Indica si una afiliación activa en este estado requiere licencia M365.",
    )

    class Meta:
        """Impone unicidad de códigos, estado inicial y coherencia de banderas."""

        ordering = ["profile_type", "code"]
        verbose_name = "Definición de Estado de Saga"
        verbose_name_plural = "Definiciones de Estados de Saga"
        constraints = [
            #FSTAT01-0010: El código es único dentro de su tipo.
            models.UniqueConstraint(
                fields=["profile_type", "code"], name="uq_status_code_per_type"
            ),
            #FSTAT02-0011: Un solo estado inicial por tipo de vinculación.
            models.UniqueConstraint(
                fields=["profile_type"],
                condition=Q(is_initial=True),
                name="uq_one_initial_status_per_type",
            ),
            #FSTAT03-0012: Un estado no puede ser inicial y terminal simultáneamente.
            models.CheckConstraint(
                condition=~(Q(is_initial=True) & Q(is_terminal=True)),
                name="ck_status_not_initial_and_terminal",
            ),
        ]

    def __str__(self):
        """Devuelve tipo y código para distinguir estados con nombres repetidos."""

        return f"{self.profile_type.code}:{self.code}"


class SagaTransitionRule(models.Model):
    """Arista dirigida que autoriza un salto entre dos estados de un perfil.

    Los servicios solo aplican transiciones persistidas aquí. La comprobación
    cruzada de tipos y la prohibición de salir de terminales se hacen en
    ``clean()``; restricciones SQL refuerzan unicidad y ausencia de autoaristas.
    """

    profile_type = models.ForeignKey(
        ProfileTypeDefinition, on_delete=models.PROTECT, related_name="transitions"
    )
    from_status = models.ForeignKey(
        SagaStatusDefinition, on_delete=models.PROTECT, related_name="outgoing_rules"
    )
    to_status = models.ForeignKey(
        SagaStatusDefinition, on_delete=models.PROTECT, related_name="incoming_rules"
    )
    action_name = models.CharField(
        max_length=100, help_text="Nombre de la acción (ej: 'Confirmar Bedelía')"
    )
    requires_cross_validation = models.BooleanField(
        default=False,
        help_text="Si es True, evalúa CrossProfileConstraint antes de transicionar.",
    )
    is_expiration = models.BooleanField(
        default=False,
        help_text="Si es True, es la regla determinista que ejecuta el barrido nocturno al vencer el TTL.",
    )

    class Meta:
        """Declara las restricciones de integridad del grafo almacenado."""

        verbose_name = "Regla de Transición de Saga"
        verbose_name_plural = "Reglas de Transición de Saga"
        constraints = [
            #FRULE01-0020: Una sola regla para cada arista dirigida.
            models.UniqueConstraint(
                fields=["profile_type", "from_status", "to_status"],
                name="uq_transition_edge",
            ),
            #FRULE02-0021: Se prohíben transiciones reflexivas directas.
            models.CheckConstraint(
                condition=~Q(from_status=models.F("to_status")),
                name="ck_transition_not_self_loop",
            ),
            #FRULE03-0022: Cada estado origen tiene como máximo una regla de vencimiento.
            models.UniqueConstraint(
                fields=["from_status"],
                condition=Q(is_expiration=True),
                name="uq_one_expiration_rule_per_status",
            ),
        ]

    def clean(self):
        """Valida que la arista pertenezca a un único grafo y no salga de terminal.

        ``full_clean()`` invoca este método antes de guardar desde formularios o
        servicios; las restricciones de base cubren las reglas representables
        en SQL. No realiza escrituras ni altera el estado de la transacción.
        """

        super().clean()
        #FRULE04-0023: Ambos extremos deben pertenecer al tipo declarado.
        for status in (self.from_status, self.to_status):
            if status and status.profile_type_id != self.profile_type_id:
                #FRULE04-0023: Rechaza arista con extremo de otro grafo.
                raise ValidationError(f"{status} no pertenece al tipo {self.profile_type}.")
        #FRULE05-0024: Ninguna transición puede salir de un estado terminal.
        if self.from_status and self.from_status.is_terminal:
            #FRULE05-0024: Rechaza un grafo que reabre un estado terminal.
            raise ValidationError("No puede salir una transición desde un estado terminal.")

    def __str__(self):
        """Representa visualmente la arista y la acción autorizada."""

        return f"{self.from_status} → {self.to_status} ({self.action_name})"


class CrossProfileConstraint(models.Model):
    """Regla de coexistencia entre afiliaciones de una misma identidad.

    ``REQUIRES`` exige otra afiliación, ``FORBIDS`` la excluye y ``MUTEX``
    expresa exclusión simétrica. ``same_reference`` limita la evaluación a la
    misma carrera, área u otra referencia funcional.
    """

    class Condition(models.TextChoices):
        """Operadores admitidos por las reglas de coexistencia."""

        REQUIRES = "REQUIRES", "Requiere"
        FORBIDS = "FORBIDS", "Prohíbe"
        MUTEX = "MUTEX", "Excluyente"

    target_profile_type = models.ForeignKey(
        ProfileTypeDefinition, on_delete=models.PROTECT, related_name="constraints_as_target"
    )
    target_status = models.ForeignKey(
        SagaStatusDefinition, on_delete=models.PROTECT, related_name="+"
    )
    required_profile_type = models.ForeignKey(
        ProfileTypeDefinition, on_delete=models.PROTECT, related_name="constraints_as_required"
    )
    required_status = models.ForeignKey(
        SagaStatusDefinition, on_delete=models.PROTECT, related_name="+"
    )
    condition_type = models.CharField(max_length=20, choices=Condition.choices)
    same_reference = models.BooleanField(
        default=False,
        help_text="True si la otra vinculación debe coincidir exactamente en el campo reference (misma carrera).",
    )

    class Meta:
        """Define las etiquetas usadas por administración y formularios."""

        verbose_name = "Restricción Cruzada de Perfil"
        verbose_name_plural = "Restricciones Cruzadas de Perfil"

    def clean(self):
        """Verifica que cada estado coincida con el tipo al que está asociado.

        Es una regla intertabla, por lo que requiere validación de aplicación y
        no se puede expresar con un ``CheckConstraint`` ordinario en PostgreSQL.
        La comprobación es de solo lectura y no introduce efectos transaccionales.
        """

        super().clean()
        #FCROSS01-0030: El estado objetivo debe corresponder a su tipo.
        if self.target_status and self.target_status.profile_type_id != self.target_profile_type_id:
            #FCROSS01-0030: Rechaza referencia incoherente del extremo objetivo.
            raise ValidationError("target_status no corresponde a target_profile_type.")
        #FCROSS02-0031: El estado requerido debe corresponder a su tipo.
        if self.required_status and self.required_status.profile_type_id != self.required_profile_type_id:
            #FCROSS02-0031: Rechaza referencia incoherente del extremo requerido.
            raise ValidationError("required_status no corresponde a required_profile_type.")

    def __str__(self):
        """Resume los extremos y operador para diagnóstico y administración."""

        return (
            f"{self.target_profile_type}:{self.target_status.code} "
            f"{self.condition_type} {self.required_profile_type}:{self.required_status.code}"
        )


# ======================================================================
# CAPA 2: IDENTIDADES Y VINCULACIONES OPERATIVAS
# ======================================================================


class Identity(models.Model):
    """Identidad raíz estable de una persona física o unidad funcional.

    El identificador único evita duplicar sujetos. Las restricciones SQL
    garantizan los requisitos simples sobre apellido y campos no vacíos incluso
    ante escrituras ORM que no llamen ``full_clean()``.
    """

    class Category(models.TextChoices):
        """Categorías de sujeto institucional admitidas."""

        FISICA = "FISICA", "Persona física"
        ORGANIZACIONAL = "ORGANIZACIONAL", "Organizacional"

    category = models.CharField(max_length=20, choices=Category.choices, db_index=True)
    identifier = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="DNI/Pasaporte para personas físicas; código de área para organizacionales.",
    )
    first_name = models.CharField(
        max_length=150, help_text="Nombres o denominación de la unidad/cátedra."
    )
    last_name = models.CharField(
        max_length=150, blank=True, help_text="Apellidos (vacío en organizacionales)."
    )
    institutional_upn = models.EmailField(
        max_length=150,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text="Cuenta oficial (@eco.unrc.edu.ar). Null mientras no esté aprovisionada.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Registra identidad por categoría y evita estados nominales inválidos."""

        verbose_name = "Identidad"
        verbose_name_plural = "Identidades"
        constraints = [
            #FID01-0040: Las identidades organizacionales no llevan apellido.
            models.CheckConstraint(
                condition=Q(category="FISICA") | Q(last_name=""),
                name="ck_org_identity_without_last_name",
            ),
            #FID02-0041: Las identidades físicas requieren apellido en PostgreSQL.
            models.CheckConstraint(
                condition=~Q(category="FISICA") | ~Q(last_name=""),
                name="ck_physical_identity_with_last_name",
            ),
            #FID03-0042: El identificador y el nombre no pueden ser cadenas vacías.
            models.CheckConstraint(
                condition=~Q(identifier="") & ~Q(first_name=""),
                name="ck_identity_required_fields",
            ),
        ]

    def clean(self):
        """Valida los nombres según la categoría antes de una escritura validada.

        Django invoca ``clean()`` desde ``full_clean()``, no desde ``save()``.
        Las restricciones SQL duplican las condiciones simples para proteger
        escrituras directas y operaciones masivas.
        """

        super().clean()
        #FID04-0043: Una persona física debe tener apellido no vacío.
        if self.category == self.Category.FISICA and not self.last_name:
            #FID04-0043: Rechaza identidad física sin apellido.
            raise ValidationError({"last_name": "El apellido es obligatorio para personas físicas."})
        #FID05-0044: Una identidad organizacional no debe tener apellido.
        if self.category == self.Category.ORGANIZACIONAL and self.last_name:
            #FID05-0044: Rechaza apellido en unidad funcional.
            raise ValidationError({"last_name": "Una identidad organizacional no lleva apellido."})

    def __str__(self):
        """Devuelve nombre y apellido sin espacios sobrantes."""

        return f"{self.first_name} {self.last_name}".strip()


class PersonalProfile(models.Model):
    """Datos personales opcionales con relación uno a uno con una identidad.

    Al eliminarse la identidad, PostgreSQL elimina su perfil de contacto por
    ``CASCADE``. La operación no altera las demás identidades.
    """

    identity = models.OneToOneField(
        Identity, on_delete=models.CASCADE, related_name="personal_profile"
    )
    personal_email = models.EmailField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=50, blank=True)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    birth_date = models.DateField(
        null=True, blank=True, help_text="Opcional. Null en funcionales."
    )

    class Meta:
        """Define las etiquetas administrativas del perfil personal."""

        verbose_name = "Perfil Personal"
        verbose_name_plural = "Perfiles Personales"

    def __str__(self):
        """Identifica el contacto por la identidad propietaria."""

        return f"Contacto de {self.identity}"


class Affiliation(models.Model):
    """Vinculación concreta de una identidad con un tipo y un estado actual.

    Los servicios de dominio son el punto autorizado para crear o transicionar
    filas. ``clean()`` protege reglas intertabla y la consulta de duplicados;
    esa consulta necesita el bloqueo de identidad en servicios para eliminar
    carreras entre altas concurrentes de tipos no múltiples.
    """

    class Modality(models.TextChoices):
        """Modalidades académicas reconocidas por el módulo."""

        PRESENCIAL = "PRESENCIAL", "Presencial"
        DISTANCIA = "DISTANCIA", "Distancia"

    identity = models.ForeignKey(
        Identity, on_delete=models.PROTECT, related_name="affiliations"
    )
    profile_type = models.ForeignKey(ProfileTypeDefinition, on_delete=models.PROTECT)
    current_status = models.ForeignKey(SagaStatusDefinition, on_delete=models.PROTECT)
    reference = models.CharField(
        max_length=150,
        blank=True,
        help_text="Código de carrera (ej: 2, 3) o secretaría. Diferencia vinculaciones simultáneas.",
    )
    modality = models.CharField(max_length=20, choices=Modality.choices, blank=True)
    start_date = models.DateField(default=timezone.localdate)
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="TTL del estado actual calculado para el barrido nocturno."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="False automáticamente al transicionar a un estado terminal.",
    )

    class Meta:
        """Agrega índices operativos para búsquedas por identidad y barrido TTL."""

        verbose_name = "Vinculación (Affiliation)"
        verbose_name_plural = "Vinculaciones (Affiliations)"
        indexes = [
            models.Index(fields=["identity", "profile_type", "is_active"]),
            #FAFF01-0050: Índice parcial de vencimientos activos para el sweeper.
            models.Index(
                fields=["expires_at"],
                condition=Q(is_active=True),
                name="ix_active_aff_by_expiry",
            ),
        ]

    def clean(self):
        """Valida la coherencia del tipo, el estado y la multiplicidad activa.

        La comprobación de multiplicidad es una lectura preliminar y por sí sola
        no es serializable; los servicios bloquean la identidad dentro de una
        transacción para que dos altas simultáneas no pasen ambas esta regla.
        """

        super().clean()
        #FAFF02-0051: El estado vigente debe pertenecer al tipo de afiliación.
        if (
            self.current_status_id
            and self.profile_type_id
            and self.current_status.profile_type_id != self.profile_type_id
        ):
            #FAFF02-0051: Rechaza el cruce de grafos de estado.
            raise ValidationError("El estado actual no pertenece al tipo de vinculación.")
        #FAFF03-0052: Tipos no múltiples admiten una sola afiliación activa por identidad.
        if (
            self.profile_type_id
            and self.identity_id
            and not self.profile_type.allows_multiple_instances
            and self.is_active
        ):
            duplicates = Affiliation.objects.filter(
                identity_id=self.identity_id,
                profile_type_id=self.profile_type_id,
                is_active=True,
            ).exclude(pk=self.pk)
            if duplicates.exists():
                #FAFF03-0052: Rechaza segunda afiliación activa del tipo.
                raise ValidationError(
                    f"{self.profile_type} no admite más de una vinculación activa simultánea."
                )

    def __str__(self):
        """Resume persona, tipo, estado y referencia para lectura humana."""

        return (
            f"{self.identity} · {self.profile_type}:{self.current_status.code} "
            f"{self.reference}"
        )


# ======================================================================
# CAPA 3: AUDITORÍA INMUTABLE (Append-only)
# ======================================================================


class OutboxEvent(models.Model):
    """Durable provisioning command emitted in the producer transaction.

    The increasing primary key supplies strict FIFO ordering for a single
    identity while ``public_id`` is safe to expose externally. Foreign keys
    preserve the source context and prevent deleting referenced domain rows.
    Checkpoint JSON may contain provider outcomes but never credentials or
    temporary passwords. State, lock, retry and heartbeat fields are written
    in short transactions by the dispatcher and saga worker.
    """

    class Status(models.TextChoices):
        """Persisted lifecycle values for dispatch and recovery."""

        PENDING = "PENDING", "Pendiente"
        PROCESSING = "PROCESSING", "En proceso"
        WAITING_SUBTASK = "WAITING_SUBTASK", "Esperando Moodle"
        RETRY_SCHEDULED = "RETRY_SCHEDULED", "Reintento programado"
        SUCCEEDED = "SUCCEEDED", "Completado"
        SIMULATED = "SIMULATED", "Completado en simulación"
        FAILED = "FAILED", "Fallo definitivo"

    id = models.BigAutoField(primary_key=True)
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    action = models.CharField(
        max_length=16, choices=SagaStatusDefinition.ProvisioningAction.choices
    )
    identity = models.ForeignKey(
        Identity, on_delete=models.PROTECT, related_name="outbox_events"
    )
    affiliation = models.ForeignKey(
        Affiliation, on_delete=models.PROTECT, related_name="outbox_events"
    )
    payload = models.JSONField(default=dict)
    step_results = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=10)
    last_error = models.TextField(blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    retry_after = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Define the table and indexes for claiming and identity FIFO."""

        db_table = "people_outbox_events"
        indexes = [
            models.Index(fields=["status", "retry_after"], name="ix_outbox_status_retry"),
            models.Index(fields=["identity", "id"], name="ix_outbox_identity_order"),
        ]

    def __str__(self):
        """Expose a PII-free summary suitable for administration and logs."""

        return f"OutboxEvent({self.public_id}, {self.action}, {self.status})"


class AffiliationStatusHistory(models.Model):
    """Bitácora append-only de altas y cambios de estado de una afiliación.

    El ORM rechaza editar o borrar instancias existentes. Cada fila registra
    actor, motivo, instante y regla aplicada; la fila debe insertarse en la
    misma transacción que la mutación de la afiliación. SQL directo y
    ``QuerySet.update()`` requieren protección externa si se necesita blindaje
    frente a código que evada el ORM.
    """

    affiliation = models.ForeignKey(
        Affiliation, on_delete=models.PROTECT, related_name="history"
    )
    from_status = models.ForeignKey(
        SagaStatusDefinition,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        help_text="Null únicamente en la fila de alta de vinculación.",
    )
    to_status = models.ForeignKey(
        SagaStatusDefinition, on_delete=models.PROTECT, related_name="+"
    )
    rule = models.ForeignKey(
        SagaTransitionRule, on_delete=models.PROTECT, null=True, blank=True
    )
    changed_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True
    )
    changed_by = models.CharField(
        max_length=150,
        help_text="Etiqueta obligatoria: 'job:ttl_sweeper', 'operador:jperez', etc.",
    )
    reason = models.CharField(max_length=255, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        """Ordena eventos cronológicamente por afiliación en consultas comunes."""

        ordering = ["affiliation", "occurred_at"]
        verbose_name = "Historial de Estado de Vinculación"
        verbose_name_plural = "Historiales de Estado de Vinculación"

    def save(self, *args, **kwargs):
        """Permite insertar eventos y rechaza sobrescribir hechos ya persistidos.

        El evento se guarda en la transacción activa del llamador; el servicio
        de dominio garantiza que la mutación y el asiento se confirmen o reviertan
        juntos. ``force_insert`` conserva la semántica append-only al crear.
        """

        if self.pk:
            #FHIST01-0060: Una fila de auditoría existente nunca se actualiza.
            raise ValidationError(
                "El historial de estados es append-only: no se puede modificar."
            )
        #FHIST03-0062: Inserta un evento nuevo en la transacción del llamador.
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Rechaza la eliminación ORM de cualquier evento de auditoría."""

        #FHIST02-0061: El historial append-only no admite eliminación ORM.
        raise ValidationError("El historial de estados es append-only: no se puede eliminar.")

    def __str__(self):
        """Expone origen, destino y actor como resumen auditable."""

        origin = self.from_status.code if self.from_status else "ALTA"
        return f"{self.affiliation_id}: {origin} → {self.to_status.code} ({self.changed_by})"
