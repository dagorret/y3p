# 05. Contingencias y preguntas frecuentes

[Índice general](00_indice_general.md) · [Anterior](04_puesta_en_marcha_y_operacion.md)

## Protocolo para operadores

Si una cuenta o un correo no llega, no repetir la inscripción. Registrar DNI, programa o curso, hora aproximada y mensaje visible; enviar esos datos al canal institucional de TI. No solicitar ni compartir contraseñas por el circuito de incidencias.

Antes de escalar un correo faltante, pedir al destinatario que revise correo no deseado y confirme la dirección personal declarada. La confirmación de inscripción no es una constancia de entrega de correo.

## Revisión técnica del correo

En el Outbox, localizar el evento y consultar sus resultados por paso:

| Resultado | Interpretación y acción |
|---|---|
| `SIMULATED` | No acredita un envío real. Revisar modo de ejecución |
| Moodle aún pendiente | El envío de credenciales ocurre después de Moodle; resolver primero esa etapa |
| `password_and_mail: SKIPPED_EXISTING_USER` | La cuenta ya existía; el sistema no cambia su clave ni envía credenciales nuevas |
| `password_and_mail: CONFIRMED` | El cliente SMTP confirmó el envío; revisar entrega posterior, filtros y dirección |
| `RETRY_SCHEDULED` | Esperar el próximo intento y revisar la causa |
| `FAILED` | Revisar credenciales de servicios, destinatario, UPN y configuración antes de rearmar |

La contraseña temporal no se guarda en el Outbox. Si falla el envío después del cambio de clave, el reintento genera otra contraseña y vuelve a intentar la etapa. No existe una función de consulta de esa contraseña ni un botón general de reenvío para trabajos exitosos.

> [!NOTE]
> El aprovisionamiento usa `MailerClient` con variables SMTP. El backend de correo de consola de Django no demuestra que ese cliente esté configurado ni que se haya enviado un mensaje real.

## Caídas de Microsoft, Moodle o de la cola

```text
PENDING --> PROCESSING --> WAITING_SUBTASK
               ^                 |
               |                 +--> PENDING (Moodle confirmado)
               |
               +--- despacho posterior

PROCESSING --> SUCCEEDED (flujo real finalizado)
PROCESSING --> SIMULATED (flujo simulado finalizado)
Error transitorio --> RETRY_SCHEDULED --> PROCESSING
Error permanente o intentos agotados --> FAILED
FAILED --> PENDING (rearme por TI después de resolver la causa)
```

Los reintentos transitorios usan una espera creciente: `min(2**intentos * 30, 3600)` segundos, más un margen aleatorio de 0 a 30 segundos. Si el proveedor indica una espera por límite de solicitudes, el orquestador respeta `Retry-After` cuando está disponible; ese caso no consume el contador de intentos. Los eventos nuevos tienen un máximo de 10 intentos por defecto.

Errores de autenticación, permisos, configuración o recursos ausentes pueden terminar directamente en `FAILED`. No se solucionan esperando indefinidamente. Una falla del broker al publicar también se registra para reintento.

Cada cinco minutos el barrido busca trabajos abandonados: `PROCESSING` sin actividad por más de cinco minutos y `WAITING_SUBTASK` sin actividad por más de dos horas. La recuperación requiere Beat y un trabajador de la cola `provisioning` disponibles.

> [!WARNING]
> Un evento `FAILED` bloquea los posteriores de la misma identidad. No modificar su estado a `SUCCEEDED` para liberar la cola: se perdería la correspondencia entre el registro y lo realmente ejecutado.

## Rearme controlado

Para un fallo real, corregir la causa y usar «Rearmar eventos seleccionados» en el administrador. La acción reinicia intentos y conserva los pasos confirmados, para continuar el flujo. Revisar primero que los datos de ejecución sean correctos; la acción no reconstruye los datos del evento.

Para convertir trabajos simulados a ejecución real, el rearme del administrador no es suficiente porque conserva los resultados simulados. Con los consumidores detenidos mientras se verifica la configuración, previsualizar:

```bash
uv run python manage.py rearm_simulated_events --dry-run
```

Tras revisar destinos y credenciales, y configurar `LUCY_DRY_RUN=False`, ejecutar:

```bash
uv run python manage.py rearm_simulated_events
```

Este comando afecta **todos** los eventos `SIMULATED`, limpia sus resultados por paso y solicita despacho después de confirmar el cambio. Reiniciar los procesos para que lean la configuración revisada. No usarlo si solo se pretende resolver un caso individual sin evaluar el resto.

## Diagnóstico y comprobación de salud — TI

Ejecutar desde la raíz del repositorio. Los nombres Docker corresponden al ejemplo del capítulo 4.

```bash
docker ps -a
docker exec y3p-postgres pg_isready -U postgres -d postgres
docker exec y3p-redis redis-cli ping
uv run python manage.py check
uv run python manage.py showmigrations people portal provisioning
uv run python manage.py makemigrations --check --dry-run
uv run celery -A config inspect ping --timeout=5
uv run celery -A config inspect active_queues --timeout=5
```

Una comprobación real de la conexión configurada en Django:

```bash
uv run python manage.py shell -c 'from django.db import connection; c = connection.cursor(); c.execute("SELECT 1"); print(c.fetchone()); c.close()'
```

Consultar un resumen del Outbox, sin procesar eventos:

```bash
uv run python manage.py shell -c 'from django.db.models import Count; from apps.people.models import OutboxEvent; print(list(OutboxEvent.objects.values("status").annotate(total=Count("id")).order_by("status")))'
```

| Comprobación | Resultado esperado y límite |
|---|---|
| PostgreSQL | Acepta conexiones; `SELECT 1` valida además las credenciales de Django |
| Redis | `PONG`; comprobar también que su dirección coincida con el broker configurado |
| Django `check` | Sin incidencias; no valida disponibilidad de todas las integraciones |
| Migraciones | `people.0001` a `0007` aplicadas; ausencia de cambios de modelos sin migración |
| Celery | Respuesta de ambos trabajadores y sus colas; no verifica Beat por sí solo |
| Beat y Outbox | Revisar consola de Beat y avance de pendientes; no hay endpoint `/health/` implementado |

La suite se ejecuta con `uv run python manage.py test`; la referencia de v0.2.0 es 57 pruebas aprobadas. Requiere PostgreSQL y permiso para crear una base temporal de pruebas. Los servicios externos se sustituyen por dobles en las pruebas: un resultado verde no acredita conectividad real con Microsoft o Moodle.

## Preguntas frecuentes

**¿Un estado PRE evita crear cuentas?** En grado, PRE no tiene acción de aprovisionamiento configurada. En posgrado y extensión, tanto PRE como REGULAR emiten PROVISION. Verificar siempre el tipo de vinculación.

**¿Por qué aparece una inscripción sin cuenta disponible?** El registro y la ejecución externa son etapas distintas. Puede faltar UPN, configuración, un trabajador o la disponibilidad de un proveedor. TI revisa los datos capturados en el evento y sus pasos.

**¿Puedo corregir el UPN desde la pantalla de personas?** El administrador lo presenta como solo lectura. v0.2.0 no ofrece un circuito operativo completo para asignarlo y reconstruir eventos ya emitidos; corresponde a una intervención técnica controlada.

**¿Cerrar una vinculación elimina la cuenta?** No necesariamente. Otras vinculaciones pueden mantener accesos; la política actual no ejecuta borrado remoto mediante TERMINATE.

**¿DigitalAccount detecta cambios automáticamente?** No. El modelo conserva la huella que un proceso le asigne; la comparación y sincronización automática aún no forman parte del circuito.
