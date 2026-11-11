# 01. Propósito y visión general

[Índice general](00_indice_general.md) · [Siguiente: entornos de trabajo](02_entornos_de_trabajo.md)

## Finalidad institucional

Y3P / MT^3 organiza la identidad digital de la comunidad de la Facultad y coordina sus accesos a Microsoft 365 y Moodle. Busca que una inscripción o un cambio de situación académica pueda seguirse desde su registro hasta su resolución, con antecedentes consultables.

El sistema reemplaza progresivamente al CRM anterior mediante módulos separados, una base PostgreSQL y trabajos en segundo plano recuperables. Este reemplazo requiere revisar datos y procedimientos; no implica que todos los procesos históricos hayan sido trasladados automáticamente.

## Regla de oro: una persona, una identidad institucional

Para personas físicas, la referencia es el DNI. El UPN es el nombre de acceso institucional, habitualmente con formato de correo. La regla operativa 1:1 UPN/DNI evita crear una cuenta institucional nueva por cada carrera, curso o función de la misma persona.

| Situación | Criterio de registro |
|---|---|
| Una persona cursa dos carreras | Una identidad y dos vinculaciones, cuando el tipo lo permite |
| Una persona estudia y ejerce docencia | Una identidad con vinculaciones distintas |
| Se registra un correo personal | Es un contacto; no reemplaza el UPN institucional |
| Se registra una unidad organizacional | Se identifica por su código, no por un DNI ficticio |

> [!NOTE]
> La base exige identificadores únicos y UPN únicos cuando están asignados. Una identidad puede existir sin UPN mientras se prepara su cuenta. DigitalAccount permite varias cuentas por identidad; no convierte cada cuenta adicional en otra persona.

Antes de repetir un alta, buscar el DNI y comprobar sus vinculaciones. Las discrepancias de identidad o cuenta deben resolverse con TI.

## Qué garantiza el Outbox

El Outbox es un registro persistente de acciones pendientes. Cuando el servicio de dominio confirma una vinculación o transición que requiere aprovisionamiento, guarda el cambio y su solicitud de trabajo en la misma operación de base de datos.

```text
Cambio institucional
       |
       v
Guardar cambio + solicitud pendiente juntos
       |
       v
Procesar servicios externos por etapas
       |
       +--> Trabajo completado
       +--> Reintento programado
       +--> Incidencia para revisión de TI
```

Así se evita confirmar el cambio local sin conservar su solicitud externa. Si el envío a la cola falla, el despacho periódico puede recuperar el trabajo guardado. Los pasos ya confirmados se registran para permitir continuar el procesamiento.

> [!WARNING]
> Una inscripción registrada no acredita que la cuenta ya esté disponible ni que el correo haya llegado al destinatario. La consistencia local no equivale a una transacción única sobre Microsoft, Moodle y correo; los servicios externos pueden completar sus pasos en momentos diferentes.

El orden se conserva por identidad: un evento anterior sin resolución puede retener los siguientes. Las autoridades deben interpretar una demora como una situación pendiente de seguimiento, no como motivo para volver a cargar a la persona.
