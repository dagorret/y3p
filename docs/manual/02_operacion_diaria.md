# 2. Operación diaria de las consolas — v0.4.6

## Buscar y recorrer la grilla

Abrir la consola del ámbito autorizado. En escritorio ocupa el ancho disponible con menú lateral anclado; en móvil, «Menú Administración» abre la navegación y Escape la cierra. Las tablas admiten desplazamiento horizontal y se respeta la preferencia de movimiento reducido.

La barra superior filtra por carrera, estado de saga, semáforo y texto libre (DNI, apellido, nombre o UPN). «Filas por página» admite 25, 50, 100 o 200; el valor inicial es 50. La paginación numerada conserva los filtros, la fórmula y el tamaño. El orden estable es apellido, nombre e identificador interno, incluso ante homónimos.

La tabla separa selección, DNI, apellido y nombre, UPN, carrera, estado, vencimiento, salud y acciones. «Indefinida» significa ausencia de fecha de vencimiento. El diagnóstico es local y puede estar retrasado respecto del proveedor.

| Semáforo | Criterio local y acción sugerida |
|---|---|
| Rojo | Evento del ámbito fallido o fallo Moodle observado con cuenta activa; revisar Outbox y motivo |
| Naranja | TTL vencido o vinculación activa con falta de licencia conocida; revisar vigencia y política |
| Verde | Cuenta activa y licenciada, matrícula observada, vínculo activo, sin cambio de clave pendiente y con actividad observada |
| Amarillo | Información insuficiente o advertencias, por ejemplo clave temporal o actividad ausente; leer el detalle del indicador |

La prioridad es rojo, naranja, verde y finalmente amarillo. No se infiere salud por ausencia de observaciones. La lectura local de Moodle refleja cursos observados; no certifica por sí sola la correspondencia pedagógica exacta de cada vinculación.

## Alta, inscripción y confirmación

1. En Posgrado o Extensión, elegir «Alta / Inscripción» y registrar la identidad, curso y tipo de inscripción. `PRE` registra una preinscripción sin aprovisionamiento. El formulario también conserva la inscripción regular del flujo previo; revisar la opción antes de confirmar.
2. Volver a la grilla y filtrar `PRE`. Seleccionar personas y usar «Confirmar Preinscriptos».
3. El servidor bloquea y valida las vinculaciones del ámbito, promueve `PRE` a `ALUMNO`, escribe historial y genera el Outbox en la misma transacción.
4. Leer «Confirmados» y «Omitidos». Los ya confirmados se omiten; repetir el lote no duplica sus eventos. Una selección ajena al ámbito se rechaza.
5. Seguir el aprovisionamiento y la bienvenida en el monitor. La confirmación académica no significa que el correo ya haya llegado.

En Grado el recorrido académico habitual es `PRE → ASP → INGRE → ALUMNO`, según las reglas del metamodelo. El alta manual crítica crea un ingresante `INGRE`; no es el botón de confirmación directa de Posgrado/Extensión. No todas las vinculaciones ni estados permiten las mismas transiciones.

La bienvenida con credenciales se ejecuta en el trabajador. Si ya existe una cuenta, este flujo puede regenerar su clave temporal: la identidad digital es compartida entre ámbitos. No copiar credenciales al formulario de notificaciones ni a observaciones.

## Ficha 360°

«Abrir Ficha 360°» lleva a la ficha del ámbito. Posgrado y Extensión muestran una ficha académica de consulta con sus vinculaciones; no ofrecen todas las herramientas maestras de Grado.

La ficha de Grado permite, según nivel, editar contacto y metadatos, consultar cuentas, equipos/canales e historial, enviar clave temporal, revocar sesiones y operar Moodle. La revocación de sesiones no elimina métodos MFA. Licencias, suspensión, transición manual y ajuste de TTL requieren administración crítica y las reglas del dominio. Una suspensión global se protege cuando otra vinculación académica mantiene accesos.

«Actualizar estado (Teams / Moodle)» realiza una consulta puntual autorizada, actualiza observaciones locales y reemplaza la ficha por HTMX. Ver equipos y canales observados no acredita pertenencia a canales privados. La bitácora de soporte conserva intervenciones; los registros de acceso se delegan a Microsoft Purview.

## Excel y comunicaciones

«Exportar a Excel» descarga todas las filas coincidentes con los filtros del ámbito, no sólo la página visible. Requiere lectura. Las columnas son las de datos de la tabla; selección y botones son controles de interfaz. La exportación evita interpretar valores de usuario como fórmulas de hoja de cálculo.

La selección se limita a la página actual y los lotes admiten hasta 200 identidades. «Enviar Notificación por Email» solicita asunto y cuerpo. Variables admitidas: `{full_name}`, `{upn}`, `{document_number}`. Revisar destinatarios y texto antes de confirmar: el servidor sustituye los valores y crea eventos `SUPPORT` atómicamente. El navegador no envía correos directamente. Grado conserva además operaciones por lote de credenciales, matrícula Moodle y licencia crítica.
