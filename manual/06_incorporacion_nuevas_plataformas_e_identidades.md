# 06. Incorporación de nuevas plataformas e identidades

[Índice general](00_indice_general.md) · [Anterior: contingencias y FAQ](05_contingencias_y_faq.md)

## Propósito y principio de extensión

Una decisión institucional puede incorporar una plataforma educativa, como Canvas o Evelia, o un proveedor de identidad y colaboración, como Google Workspace. La implementación debe concentrar el cambio en los puntos de integración y conservar el núcleo institucional.

**Principio: el 90 % del sistema permanece intacto y no se toca.** Personas por DNI, generador UUIDv8 semántico, Outbox transaccional, portales HTMX, infraestructura Celery y Backoffice Unfold mantienen su función y sus contratos. La incorporación de un proveedor no exige rediseñarlos ni duplicarlos.

El porcentaje expresa un criterio de arquitectura, no una medición de líneas de código. El catálogo y la conexión del nuevo adaptador se modifican de manera localizada; sus migraciones y pruebas forman parte del cambio. El administrador existente puede presentar las nuevas opciones sin reconstruir sus pantallas.

| Decisión institucional | Dominio afectado | Qué conserva su significado |
|---|---|---|
| Incorporar Canvas o Evelia | Destino pedagógico de una vinculación | Persona, DNI y trayectoria institucional |
| Incorporar Google Workspace | Proveedor de cuenta digital e identidad externa | Identidad local y sus vinculaciones |
| Trasladar un entorno o tenant completo | Operación administrativa de migración | Historial, correspondencias y trazabilidad de las personas |

Aquí «nuevo proveedor de identidad» significa un sistema externo que administra cuentas; no implica crear otra persona local para el mismo DNI ni habilitar automáticamente un nuevo método de inicio de sesión web.

> [!NOTE]
> Este capítulo describe cómo desarrollar una extensión. No acredita que Canvas, Evelia o Google tengan un adaptador operativo. Los campos de configuración por sí solos no habilitan una integración.

## Los cuatro puntos de intervención

### 1. Catálogo y metamodelo

El primer paso es acordar una clave estable y ubicarla en el catálogo correspondiente:

| Catálogo | Finalidad | Ejemplo de ampliación |
|---|---|---|
| `SagaStatusDefinition.target_platform` | Destino pedagógico del vínculo | Agregar `CANVAS` o `EVELIA` cuando su integración esté aprobada |
| `DigitalAccount.platform` | Plataforma a la que pertenece una cuenta | Usar `GOOGLE`, ya disponible; agregar otra clave si corresponde |

El catálogo pedagógico actual se limita a `MOODLE` y `OTRO` («Otro / Sin plataforma activa»), con `MOODLE` como valor predeterminado. No registrar destinos nuevos mediante valores arbitrarios que el modelo no admite. Las opciones son `TextChoices` definidas en código: agregar una clave requiere modificar el modelo y generar/aplicar su migración; no basta con escribirla en Unfold.

Los indicadores `requires_collaboration_account` y `requires_collaboration_license` expresan requisitos independientes de la marca del proveedor. El alias temporal `requires_m365_license` conserva compatibilidad con consumidores anteriores. Una decisión de licencia no debe confundirse con el destino pedagógico.

> [!WARNING]
> El orquestador actual todavía ejecuta pasos concretos de Graph y Moodle. Seleccionar `OTRO` o desmarcar un indicador no garantiza que se omita una llamada: la extensión debe conectar explícitamente el destino y los requisitos con el comportamiento de ejecución. No iniciar un procesamiento masivo suponiendo que esa selección ya implementa el enrutamiento.

### 2. Adaptador puro en `apps/integrations/services/`

Crear el cliente del proveedor heredando de `BaseIntegrationClient`. Su responsabilidad es hablar con la API y traducir sus respuestas; no consultar modelos Django, modificar vinculaciones, escribir en DigitalAccount ni decidir transiciones.

El contrato del cliente debe incluir:

- Dependencias inyectables para transporte, tiempo de espera y registro de eventos; construcción sin conexiones de red.
- Resultados mediante DTOs inmutables, siguiendo `@dataclass(frozen=True)` en `apps/integrations/dataclasses.py`, con identificación explícita de simulación.
- Traducción de errores de autenticación, permisos, límites de solicitudes y disponibilidad a las excepciones de integración del proyecto.
- Operaciones repetibles sin duplicar cuentas o matrículas, según las capacidades del proveedor.
- Bypass estricto de `dry_run` en cada escritura, antes de obtener tokens, abrir conexiones o invocar el transporte.

```text
Solicitud de escritura
        |
        +--> dry_run=True --> Resultado simulado, sin token ni red
        |
        +--> dry_run=False --> Validación --> API --> DTO o error tipado
```

La clase base proporciona `_intercept_dry_run`; el adaptador debe invocarlo y retornar el resultado simulado antes de continuar. Heredar la clase no intercepta automáticamente los métodos nuevos. Las lecturas requieren una política explícita si también deben funcionar sin red.

### 3. Orquestador y registro de cuentas

Conectar el cliente en `apps/provisioning/orchestrator.py`, dentro del paso correspondiente. La selección debe distinguir la plataforma pedagógica del proveedor de colaboración y respetar los requisitos definidos por el estado. La generación de eventos debe capturar los datos necesarios para que un reintento conserve el destino acordado al emitir la solicitud.

La llamada remota se realiza fuera de transacciones largas de base de datos. Tras confirmar el resultado, una transacción corta registra el avance y la cuenta asociada a la identidad. Conservar el orden por identidad, los reintentos y los resultados por paso del Outbox.

```text
Evento con destino explícito
        |
        v
Seleccionar adaptador y ejecutar paso
        |
        +--> Simulado --> Resultado de simulación; no acreditar cuenta real
        |
        +--> Confirmado --> Asociar DigitalAccount + registrar avance
        |
        +--> Fallo --> Política de reintento o revisión técnica
```

Asentar la credencial generada significa registrar su **identificador de cuenta**: UPN, correo institucional o nombre de usuario en `DigitalAccount.account_identifier`, junto con plataforma e identidad. Respetar la unicidad global plataforma/identificador y verificar el propietario antes de reutilizar una cuenta existente.

> [!WARNING]
> DigitalAccount no almacena contraseñas, tokens ni secretos. Tampoco deben persistirse en DTOs de resultado, payloads, resultados por paso o logs. Las claves temporales se manejan de forma efímera; los tokens se suministran mediante configuración protegida.

La persistencia automática de DigitalAccount desde el orquestador aún debe implementarse al desarrollar esta extensión. Su inline de Unfold permite auditar lo registrado, pero no crea cuentas remotas. `content_hash` puede utilizarse para comparar contenido normalizado: la función existe, aunque la sincronización basada en esa comparación no es automática.

Un fallo entre la confirmación remota y la escritura local puede causar la repetición del paso. El adaptador y la asociación local deben tolerarla; el Outbox no convierte todas las APIs externas en una única transacción.

### 4. Configuración de entorno

Declarar la lectura de las variables nuevas en `config/settings.py` y suministrarlas al cliente desde su punto de construcción. Documentar sus nombres en `.env.example`, sin incluir valores reales. Esta conexión inicial forma parte de la extensión; luego cambiar URLs, tenant o tokens en `.env` no debe exigir modificar la lógica del núcleo.

| Dato | Criterio de configuración |
|---|---|
| URL base | Distinguir pruebas de entorno institucional |
| Tenant o dominio | Selección explícita del ámbito administrativo |
| Token, identificador de cliente y secreto | Inyección protegida; nunca versionar secretos |
| Modo de simulación | Usar `LUCY_DRY_RUN` y verificar que llegue al adaptador |
| Permisos y licencias | Configurar según las capacidades habilitadas por la institución |

Agregar una variable en `.env` no tiene efecto si el código no la lee. Reiniciar los procesos que consumen la configuración después de cambiarla. La aplicación de mocks y el modo dry-run son mecanismos distintos.

## Validación antes de habilitar una plataforma

Comprobar el contrato del cliente con transporte simulado, incluida la ausencia de llamadas de escritura y obtención de tokens en dry-run. Verificar además el enrutamiento, las operaciones repetidas, la asociación de cuentas y la recuperación de fallos. Ejecutar el chequeo Django, revisar migraciones y aprobar la suite completa antes de habilitar el destino.

La autoridad define quién debe acceder, a qué entorno y desde cuándo. TI traduce esa decisión a catálogo, permisos, configuración y validación técnica. La disponibilidad de una clave en un catálogo no reemplaza la aprobación ni la prueba del circuito completo.

## La mudanza de plataforma es una operación administrativa independiente

Mover una cohorte de Moodle a Canvas, o trasladar cuentas entre tenants, puede involucrar cursos, matrículas, datos, permisos y correspondencias entre identificadores externos. Esa operación tiene alcance colectivo y requiere un plan propio.

No debe ejecutarse como efecto colateral de cambiar un estado cotidiano de una vinculación. Cambiar el destino de un estado no migra los datos existentes ni reescribe los eventos ya emitidos. Usarlo como disparador implícito podría repetir traslados, retirar accesos prematuramente o afectar a personas con otras vinculaciones activas.

| Saga diaria | Mudanza administrativa |
|---|---|
| Procesa una acción vinculada a una trayectoria individual | Selecciona explícitamente un conjunto de personas y recursos |
| Aplica reglas de alta, acceso o suspensión | Concilia cuentas, cursos y contenidos entre entornos |
| Continúa por pasos y reintenta fallos | Añade planificación por lotes, validación, corte y recuperación |
| Conserva el destino registrado en su evento | Define un mapa documentado de origen y destino |

```text
Decisión institucional
        |
        v
Inventario y correspondencias --> Simulación del alcance
        |
        v
Piloto validado --> Ejecución por lotes --> Conciliación final
        |
        v
Confirmación del nuevo entorno --> Retiro controlado del anterior
```

La mudanza debe registrar responsables, alcance, respaldo, criterios de aceptación y estrategia de recuperación. Puede reutilizar adaptadores y mecanismos de ejecución, pero requiere una herramienta o procedimiento administrativo explícito: este capítulo no presenta un comando de migración masiva ya implementado.

Para operación cotidiana, consultar [ciclo de vida y estados](03_ciclo_de_vida_y_estados.md); para incidentes de ejecución, [contingencias y FAQ](05_contingencias_y_faq.md).
