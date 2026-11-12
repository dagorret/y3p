# 4. Outbox transaccional y recuperación — v0.4.6

El cambio de vinculación y la intención de trabajo externo se escriben juntos en PostgreSQL. La llamada al proveedor ocurre después, desde Celery. Así, una caída de Microsoft, Moodle o SMTP no bloquea el formulario ni deja un cambio académico confirmado sin intención registrada.

El despacho reclama eventos con bloqueos breves y respeta el orden por identidad. Los pasos confirmados se conservan para reintentos. `PROVISION` puede crear cuenta, aplicar la política de licencia, continuar matrícula en otro trabajador y finalmente enviar bienvenida; `SUSPEND` / `TERMINATE` aplican las bajas correspondientes. `SUPPORT` ejecuta acciones operativas autorizadas, incluidas notificaciones. No hay una transacción distribuida capaz de revertir todos los proveedores: se usa una saga con puntos de control e idempotencia.

## Monitor e indicador

Ruta: `/dash/administracion/outbox/`. Requiere operación del ámbito; el enlace conserva `scope` cuando corresponde. Las tarjetas resumen `PENDING`, `PROCESSING`, `SUCCEEDED` y `FAILED`. Los filtros permiten consultar los demás estados y las operaciones. El listado muestra fecha, identidad, ámbito, operación, intentos y último error con paginación.

El indicador lateral se carga mediante una petición HTMX independiente y se actualiza cada diez segundos. Cuenta pendientes o fallidos autorizados; no suma todas las tareas en ejecución o espera. Este seguimiento no agrega consultas síncronas a las siete de la grilla.

| Estado persistente | Lectura operativa |
|---|---|
| `PENDING` | Disponible para despacho |
| `PROCESSING` | Reclamado por un trabajador |
| `WAITING_SUBTASK` | Espera una continuación, por ejemplo Moodle |
| `RETRY_SCHEDULED` | Espera la ventana de reintento |
| `SUCCEEDED` | Operación confirmada por el flujo |
| `SIMULATED` | Escrituras simuladas; no certifica cambios externos |
| `FAILED` | Requiere diagnóstico y eventual rearme |

## Protocolo de fallo y rearme

1. Leer estado, operación y error; comprobar disponibilidad del proveedor y trabajadores. No repetir altas desde el formulario para reemplazar un evento fallido.
2. Resolver la causa y verificar el ámbito. Sólo administración crítica del ámbito puede rearmar `FAILED`; el superusuario puede operar institucionalmente.
3. Elegir «Rearmar evento» o seleccionar varios y «Rearmar seleccionados». El lote se valida atómicamente; un identificador ajeno al ámbito se rechaza.
4. El rearme devuelve a `PENDING`, limpia error, intentos y bloqueo de ejecución; conserva los pasos confirmados y el solicitante original. La bitácora registra al operador que rearmó y los intentos previos.
5. Seguir la reejecución hasta el estado final. Rearmar un evento que ya no está `FAILED` se omite.

Las contraseñas temporales se generan para su uso en memoria. No deben persistirse en `payload`, `step_results`, `last_error`, bitácora ni registros. La auditoría E2E prueba este recorrido con secretos canarios y fallo SMTP. No pegar credenciales en cuerpos de correo libres ni mensajes de diagnóstico; el saneamiento no convierte esos campos en un gestor de secretos.
