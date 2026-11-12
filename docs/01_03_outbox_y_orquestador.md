# Outbox y orquestación de aprovisionamiento — v0.4.6

[Índice documental](README.md) · [Manual relacionado](manual/04_outbox_y_recuperacion.md)

## Persistencia y ejecución

`OutboxEvent` reside en `apps/people/models.py` y guarda operación, identidad/vinculación, ámbito de carga, solicitante, estado, intentos, error, bloqueo, latido y pasos confirmados. El evento se crea dentro de la transacción del dominio. Contraseñas, tokens y credenciales de transporte no forman parte de sus datos persistentes.

`apps/provisioning/dispatcher.py` reclama filas mediante `select_for_update(skip_locked=True)` con transacciones breves, respeta el orden por identidad y publica tareas. La llamada de red se ejecuta después del reclamo. Un evento fallido previo puede bloquear posteriores de la misma identidad hasta su resolución; no ignorar una cola detenida reintentando altas.

`apps/provisioning/orchestrator.py` coordina PROVISION, SUSPEND, TERMINATE y SUPPORT. La matrícula Moodle continúa en la cola `moodle` y vuelve a `PENDING` para completar el flujo. La entrega de bienvenida se ejecuta después de los pasos requeridos; los resultados simulados quedan como `SIMULATED`.

## Recuperación y permisos

La agenda despacha cada minuto y revisa eventos trabados cada cinco. El monitor permite rearme de FAILED con autorización crítica por ámbito y registro del operador. La ejecución de soporte revalida permisos; una capacidad revocada después de encolar no debe permitir ejecutar la operación.

El capítulo 4 presenta todos los estados y el procedimiento operativo; el capítulo 6 reúne comandos y topología. El barrido TTL cada quince minutos es independiente del barrido de eventos trabados. La sincronización de catálogos usa otra agenda por origen.
