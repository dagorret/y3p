# 5. Espejos relacionales y automatización — v0.4.6

## Catálogos de Teams y Moodle

Los selectores consultan tablas locales: `TeamsWorkspaceMirror`, `TeamsChannelMirror`, `TeamsMemberMirror` y `MoodleCourseMirror`. Esto permite trabajar sin hacer llamadas remotas al abrir cada formulario. La marca `synced_at` indica la antigüedad de la observación; un espejo no sustituye al proveedor como autoridad del acceso efectivo.

Los miembros usan una tabla pivot con FK al equipo y FK opcional a `Identity`. Se puede consultar pertenencia, listar equipos de una persona, contar integrantes y detectar invitados sin recorrer JSON. La vinculación se intenta por UPN normalizado o identificador Azure observado; `identity=None` conserva invitados externos.

| Restricción o índice | Propósito |
|---|---|
| Único `(workspace, upn)` | Evitar duplicados de un integrante en un equipo |
| Compuesto `(identity, workspace)` | Consultar equipos de una persona y pertenencias |
| Compuesto `(workspace, role)` | Separar propietarios y miembros por equipo |
| Individuales en Azure ID, UPN y rol | Búsqueda y enlace de personas observadas |
| `TeamsWorkspaceMirror.members_count` | Buscar equipos pequeños o con un integrante |

Los índices son B-tree: mejoran búsquedas, pero no garantizan complejidad O(1). Los canales estándar y privados se catalogan por equipo; el catálogo de un canal privado no acredita miembros de ese canal.

## Sincronización y agenda

`sync_external_catalogs()` consulta todos los equipos, canales, propietarios y miembros de Graph y los cursos de Moodle, siguiendo paginaciones. Obtiene los datos remotos antes de la transacción local, actualiza por origen de forma idempotente, elimina miembros/canales/equipos/cursos ya ausentes de una lectura completa y recalcula propietarios y miembros.

Si un origen falla, conserva su fotografía previa y registra el error; el otro puede completarse. Una concesión temporal de ejecución impide escrituras de un trabajador vencido. No ejecutar lecturas parciales como si fueran inventarios completos.

`CatalogSyncState` conserva agenda, última ejecución, próxima ejecución, resultado, cantidad y error por `TEAMS` / `MOODLE`. El valor inicial es sábado a las 01:00 en la zona horaria configurada. Beat revisa cada minuto si corresponde sincronizar. La pantalla `/dash/administracion/sincronizacion/` permite ajustar día/hora y encolar «Sincronizar Catálogos Ahora» con administración crítica de Grado.

La gestión de Teams permite correspondencias explícitas con cursos/comisiones y limpieza que conserva propietarios. El conteo de paridad es orientativo y distingue miembros de propietarios. Los registros de acceso de usuarios permanecen fuera de este catálogo, delegados a Purview.

## Vencimientos TTL

Celery Beat ejecuta `people.process_expired_affiliations` cada quince minutos (`crontab(minute='*/15')`), con lote predeterminado de 500. Selecciona vinculaciones activas con `expires_at <= now`, busca una regla `is_expiration=True`, bloquea y revalida cada fila en una transacción breve. Las huérfanas sin regla se informan y no bloquean el resto. Cada vinculación expirada genera la intención de suspensión correspondiente sin duplicarla al repetir el barrido.

La baja externa respeta accesos compartidos: otra vinculación vigente puede conservar cuenta/licencia y, si usa el mismo curso, matrícula Moodle. Si vence la última vinculación activa, se aplica la baja efectiva según la política. El comando de previsualización no reemplaza Beat ni procesa trabajos remotos: permite revisar candidatos antes de una intervención manual.
