# 02. Entornos de trabajo

[Índice general](00_indice_general.md) · [Anterior](01_proposito_y_vision_general.md) · [Siguiente](03_ciclo_de_vida_y_estados.md)

## Portales para secretarías

Las pantallas bajo `/dash/` permiten registrar o consultar información sin ingresar al administrador técnico. Las actualizaciones HTMX muestran resultados y mensajes dentro de la misma página.

| Dirección | Uso disponible en v0.2.0 |
|---|---|
| `/dash/administracion/` | Buscar personas con vinculación de grado por DNI, nombre o apellido; hasta 50 resultados |
| `/dash/postgrado/` | Consultar las últimas 50 vinculaciones de posgrado y registrar una inscripción |
| `/dash/extension/` | Consultar las últimas 50 vinculaciones de extensión y registrar una inscripción |

Ingresar a una dirección completa: `/dash/` sola no tiene una pantalla asignada.

Para inscribir en posgrado o extensión:

1. Revisar que la persona y la inscripción no estén ya registradas.
2. Completar DNI, nombres, apellidos, correo personal y programa o curso.
3. Elegir el estado inicial permitido: `REGULAR` o `PRE` (preinscripto).
4. Enviar una vez y revisar el mensaje de confirmación o los campos señalados.
5. Si hay demora en la cuenta, comunicar la incidencia a TI con DNI, programa y hora; no repetir el alta para forzar el envío.

El formulario acepta DNI de 7 u 8 dígitos y normaliza puntos y espacios. La selección del estado no asigna automáticamente un UPN. Ambos estados del portal están configurados para solicitar aprovisionamiento.

> [!WARNING]
> Los portales de v0.2.0 no incorporan autenticación ni autorización por secretaría en sus vistas. Su disponibilidad debe quedar limitada al entorno autorizado por la institución. No interpretar la separación de pantallas como una separación de permisos.

## Backoffice para TI

El acceso se realiza en `/admin/`, con usuario habilitado para administración. Unfold presenta personas, vinculaciones, antecedentes y Outbox. Las identidades y vinculaciones se consultan allí; los campos de negocio están protegidos frente a la edición ordinaria.

| Perfil técnico | Acceso esperado |
|---|---|
| Personal con permisos de consulta | Pantallas y datos autorizados por sus permisos Django |
| Personal con permiso de cambio de Outbox | Acciones de rearme y despacho |
| Superusuario | Configuración crítica de tipos, estados y reglas |

Los permisos deben ser asignados por TI. La configuración crítica no es una tarea de secretaría.

## Interpretar los estados del Outbox

Las etiquetas visuales o *badges* resumen el estado; siempre leer también el texto.

| Estado | Presentación | Interpretación |
|---|---|---|
| `PENDING` | Etiqueta de pendiente | Espera despacho |
| `PROCESSING` | Informativa | Se está procesando |
| `WAITING_SUBTASK` | Informativa | Espera la etapa Moodle |
| `RETRY_SCHEDULED` | Advertencia | Reintento previsto para `retry_after` |
| `SUCCEEDED` | Éxito | Flujo completado según sus pasos confirmados u omitidos |
| `SIMULATED` | Púrpura | Finalizó una simulación; no acredita ejecución real |
| `FAILED` | Peligro | Requiere revisión técnica; retiene trabajos posteriores de esa identidad |

TI puede filtrar por estado, acción o fecha y buscar por identificador público, DNI o UPN. El detalle muestra intentos, último error, datos de ejecución y resultados por paso.

> [!NOTE]
> «Encolar despacho del outbox» solicita un despacho general de trabajos elegibles, no exclusivamente de las filas seleccionadas. «Rearmar eventos seleccionados» solo actúa sobre `FAILED` y `SIMULATED`; conserva resultados por paso. Para pasar de simulación a ejecución real, seguir el procedimiento específico del capítulo 5.
