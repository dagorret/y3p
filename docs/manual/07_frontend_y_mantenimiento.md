# 7. Organización del frontend y mantenimiento — v0.4.6

## Composición de plantillas

`portal/base.html` contiene documento, recursos y navegación superior. `administracion/base_administracion.html` agrega el marco operativo de ancho completo y la barra lateral. Las páginas de Grado, Posgrado y Extensión comparten composición; el servidor determina ámbito, permisos y destinos.

Los parciales están en `apps/portal/templates/portal/fragments/`. Los principales son:

- `student_filters.html`: filtros y exportación, incluido reemplazo fuera de banda tras aplicar un snippet.
- `administracion_results.html`: raíz `#tabla-alumnos-container` y composición del formulario.
- `student_bulk_toolbar.html`, `student_table.html`, `student_row.html`: selección, estructura y columnas.
- `student_navigation.html`: paginación y snippets.
- `outbox_monitor.html` y `outbox_table.html`: filtros, resumen y eventos del monitor.
- `identity_contact.html`, `identity_support.html`, `identity_teams.html`, `identity_affiliations.html`, `identity_moodle.html`: secciones de la ficha maestra.
- `teams_activity.html`: bitácora del portal, separada del inventario.

Las cabeceras documentan responsabilidad, destino HTMX y permisos. Mantener comentarios en castellano y etiquetas `#F<MODULO><REGLA>-<ID>`. La composición no debe introducir nuevas consultas al recorrer relaciones; preparar los datos antes de renderizar.

## Contratos HTMX que deben preservarse

| Anclaje | Función |
|---|---|
| `#tabla-alumnos-container` | Sustitución completa de resultados y operaciones por lote |
| `#student-query-panel` / `#student-filters` | Filtros y actualización fuera de banda de consultas guardadas |
| `#query-builder` | Editor y vista previa integrados |
| `#identity-card` | Refresco y acciones de la ficha maestra |
| `#outbox-monitor` | Filtros, paginación y rearme autorizado |
| `#outbox-badge` | Conteo diferido y periódico |
| `#teams-panel` | Gestión y observaciones de equipos |

Conservar `hx-get`, `hx-post`, `hx-target`, `hx-swap` y `hx-push-url` al mover bloques. Las operaciones de escritura deben mantener CSRF. Los permisos de visibilidad nunca sustituyen los chequeos del servicio.

## JavaScript propio

Los módulos estáticos se cargan una sola vez con `defer` desde la base:

| Archivo en `static/portal/js/` | Responsabilidad |
|---|---|
| `admin_navigation.js` | Abrir/cerrar menú, atributos ARIA, Escape y cambio de ancho |
| `student_grid.js` | Selección total/parcial, contador y barra de acciones |
| `student_export.js` | Construir el enlace de Excel con el formulario actual |

La grilla y la exportación usan delegación de eventos para tolerar reemplazos HTMX sin manejadores duplicados ni referencias obsoletas. No reinsertar scripts o atributos `onclick` en respuestas parciales. Tailwind y HTMX siguen siendo dependencias externas cargadas por CDN; su empaquetado, disponibilidad y política CSP se deben resolver para producción. Los estilos administrativos permanecen declarativos en las bases/ficha y se presentan con formato legible.

## Formato preventivo

Usar dos espacios en HTML, etiquetas estructurales separadas, atributos largos repartidos y líneas en blanco entre secciones. Una plantilla que mezcle varios paneles y crezca sobre 200 líneas debe dividirse por responsabilidad. Los atributos largos de clases utilitarias o expresiones de permisos pueden superar 120 caracteres; eso no justifica compactar filas o tablas enteras.

El formato se verificó con djLint **1.50.0**, perfil Django. Es una herramienta temporal de mantenimiento, no una dependencia de ejecución. Configuración reproducible en `djlint.toml`; verificar con `djlint apps/portal/templates templates --check`. La suite valida resolución de estáticos y ausencia de JavaScript embebido; las pruebas Node comprueban selección después de HTMX, exportación de filtros y accesibilidad del menú. No equivalen a una prueba visual en navegadores reales.

La radiografía enumera todos los archivos mayores de 200 líneas sin afirmar que el tamaño sea por sí solo un defecto. El archivo más extenso es una suite de regresión; un eventual fraccionamiento debe respetar sus escenarios y datos, en otro cambio medido.
