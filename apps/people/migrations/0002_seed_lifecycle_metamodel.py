"""Carga inicial del grafo institucional de afiliaciones FCE / UNRC.

La migración usa modelos históricos y operaciones idempotentes para que los
códigos de tipo, estado y regla queden disponibles junto al esquema. El reverso
elimina exclusivamente los datos semilla y respeta las FK protegidas: si ya hay
afiliaciones que los usan, PostgreSQL impedirá borrar estados referenciados.
"""

from django.db import migrations


def seed_lifecycle_metamodel(apps, schema_editor):
    """Crea tipos, estados y aristas base con sus TTL y banderas de terminal.

    ``update_or_create`` permite aplicar el mismo contenido sin duplicar códigos.
    Cada entidad se escribe en la transacción de la migración; un conflicto con
    las restricciones únicas revierte el conjunto de la migración.
    """

    ProfileType = apps.get_model("people", "ProfileTypeDefinition")
    Status = apps.get_model("people", "SagaStatusDefinition")
    Rule = apps.get_model("people", "SagaTransitionRule")
    database = schema_editor.connection.alias

    #FSEED01-4001: Tipos institucionales estables; ALUMNO admite carreras simultáneas.
    profile_specs = {
        "ALUMNO": {
            "name": "Alumno",
            "is_organizational": False,
            "allows_multiple_instances": True,
        },
        "PROFESOR": {
            "name": "Profesor",
            "is_organizational": False,
            "allows_multiple_instances": False,
        },
        "INSTITUCIONAL": {
            "name": "Institucional",
            "is_organizational": True,
            "allows_multiple_instances": False,
        },
    }
    profiles = {}
    for code, defaults in profile_specs.items():
        #FSEED02-4002: Actualiza por clave de negocio para mantener semilla idempotente.
        profiles[code], _ = ProfileType.objects.using(database).update_or_create(
            code=code, defaults=defaults
        )

    #FSEED03-4003: TTL recientes de ALUMNO siguen la instrucción solicitada.
    status_specs = {
        "ALUMNO": [
            ("PRE", "Preinscripto", True, False, 60),
            ("ASP", "Aspirante", False, False, 180),
            ("INGRE", "Ingresante", False, False, 365),
            ("ALUMNO", "Alumno regular", False, False, None),
            ("BAJA_PRE", "Baja de preinscripción", False, True, None),
            ("BAJA_ASP", "Baja de aspirante", False, True, None),
            ("BAJA_INGRE", "Baja de ingresante", False, True, None),
            ("TRAMITE_TITULO", "Trámite de título", False, False, 365),
            ("GRADUADO", "Graduado", False, True, None),
        ],
        "PROFESOR": [
            ("SI", "Servicio activo", True, False, None),
            ("NO", "Servicio finalizado", False, True, None),
        ],
        "INSTITUCIONAL": [
            ("ACTIVA", "Unidad activa", True, False, None),
            ("DISUELTA", "Unidad disuelta", False, True, None),
        ],
    }
    statuses = {}
    for profile_code, rows in status_specs.items():
        profile = profiles[profile_code]
        for code, name, initial, terminal, ttl_days in rows:
            #FSEED04-4004: El código y tipo identifican el estado en la semilla.
            statuses[(profile_code, code)], _ = Status.objects.using(database).update_or_create(
                profile_type=profile,
                code=code,
                defaults={
                    "name": name,
                    "is_initial": initial,
                    "is_terminal": terminal,
                    "default_ttl_days": ttl_days,
                },
            )

    #FSEED05-4005: Aristas de ciclo de vida y únicas salidas automáticas de TTL.
    rule_specs = [
        ("ALUMNO", "PRE", "ASP", "Confirmar Bedelía", False),
        ("ALUMNO", "PRE", "BAJA_PRE", "Vencimiento de preinscripción", True),
        ("ALUMNO", "ASP", "INGRE", "Matricular Ingreso", False),
        ("ALUMNO", "ASP", "BAJA_ASP", "Vencimiento de aspirante", True),
        ("ALUMNO", "INGRE", "ALUMNO", "Avanzar Grado", False),
        ("ALUMNO", "INGRE", "BAJA_INGRE", "Vencimiento de ingresante", True),
        ("ALUMNO", "ALUMNO", "TRAMITE_TITULO", "Trámite Egreso", False),
        ("ALUMNO", "TRAMITE_TITULO", "GRADUADO", "Vencimiento de trámite de título", True),
        ("PROFESOR", "SI", "NO", "Cese", False),
        ("INSTITUCIONAL", "ACTIVA", "DISUELTA", "Disolver Área/Cátedra", False),
    ]
    for profile_code, source_code, target_code, action, expiration in rule_specs:
        profile = profiles[profile_code]
        source = statuses[(profile_code, source_code)]
        target = statuses[(profile_code, target_code)]
        #FSEED06-4006: Identidad de regla garantiza una sola arista por par origen-destino.
        Rule.objects.using(database).update_or_create(
            profile_type=profile,
            from_status=source,
            to_status=target,
            defaults={
                "action_name": action,
                "requires_cross_validation": False,
                "is_expiration": expiration,
            },
        )


def unseed_lifecycle_metamodel(apps, schema_editor):
    """Elimina las reglas semilla y luego sus estados y tipos, sin tocar otros datos.

    Las FK ``PROTECT`` evitan perder configuración que ya esté siendo usada por
    afiliaciones operativas. La eliminación corre dentro de la transacción de
    reversión de la migración y, si una referencia existe, se revierte completa.
    """

    ProfileType = apps.get_model("people", "ProfileTypeDefinition")
    Status = apps.get_model("people", "SagaStatusDefinition")
    Rule = apps.get_model("people", "SagaTransitionRule")
    database = schema_editor.connection.alias
    codes = ["ALUMNO", "PROFESOR", "INSTITUCIONAL"]

    #FSEED07-4007: Retira primero aristas semilla, respetando la dependencia FK.
    Rule.objects.using(database).filter(profile_type__code__in=codes).delete()
    #FSEED08-4008: Estados usados por otros datos no se borran por protección FK.
    Status.objects.using(database).filter(profile_type__code__in=codes).delete()
    ProfileType.objects.using(database).filter(code__in=codes).delete()


class Migration(migrations.Migration):
    """Aplica el grafo semilla después de crear las tablas del módulo."""

    dependencies = [
        ("people", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            seed_lifecycle_metamodel,
            reverse_code=unseed_lifecycle_metamodel,
        ),
    ]
