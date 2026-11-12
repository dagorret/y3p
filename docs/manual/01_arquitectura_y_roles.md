# 1. Arquitectura tripartita y permisos — v0.4.6

Y3P administra una identidad institucional por persona y varias vinculaciones académicas. Una persona puede pertenecer a más de un ámbito; cada consola consulta y opera únicamente las vinculaciones de su ámbito. Las tablas de identidad y cuentas digitales son compartidas, por lo que suspender una cuenta exige considerar todos sus accesos vigentes.

| Ámbito | Consola | Código de vinculación | Grupos predefinidos |
|---|---|---|---|
| Grado | `/dash/administracion/` | `ALUMNO` | `adm_alumnos_rol_jefe`, `adm_alumnos_rol_operador`, `adm_alumnos_rol_admin` |
| Posgrado | `/dash/postgrado/` | `POSGRADO` | `adm_posgrado_rol_jefe`, `adm_posgrado_rol_operador`, `adm_posgrado_rol_admin` |
| Extensión | `/dash/extension/` | `EXTENSION` | `adm_extension_rol_jefe`, `adm_extension_rol_operador`, `adm_extension_rol_admin` |

La ruta de Posgrado lleva «t»: `postgrado`. El código de ámbito y los grupos mantienen `POSGRADO` / `posgrado`.

## Jerarquía real de autorización

| Nivel | Grado: permiso en `people` | Posgrado / Extensión | Facultades |
|---|---|---|---|
| Consulta (`jefe`) | `can_view_student_360` | `can_view_posgrado` / `can_view_extension` | Buscar, consultar fichas del ámbito, exportar y aplicar vistas visibles |
| Operación (`operador`) | `can_operate_student_daily` | `can_operate_posgrado` / `can_operate_extension` | Incluye consulta; comunicaciones, snippets y acciones diarias del ámbito |
| Administración (`admin`) | `can_administrate_student_critical` | `can_admin_posgrado` / `can_admin_extension` | Incluye operación; decisiones críticas y rearme del Outbox del ámbito |
| Superadministración | `is_superuser=True` | Todos los ámbitos | Navegación institucional y monitor mezclado de todos los ámbitos |

Un nivel superior implica los inferiores aun cuando el permiso se asigne directamente. Un administrador de Grado no es administrador global. `is_staff` no otorga por sí solo acceso operativo. El portal requiere usuario activo y permisos del ámbito; el administrador técnico `/admin/` exige además las condiciones de acceso de Django (`is_staff` y permisos correspondientes). No se publica ninguna contraseña inicial en este manual.

## Navegación y separación de datos

La barra superior muestra sólo los ámbitos con capacidad de consulta. Un usuario exclusivo de Grado no ve Posgrado ni Extensión; el superusuario ve los tres. La barra lateral reúne herramientas del ámbito activo, sin duplicar selectores de ámbito. Las herramientas globales de Grado —catálogos, Teams, cuentas funcionales y alta manual— exigen administración crítica de Grado.

Los enlaces y controles acompañan las autorizaciones del servidor. Las rutas fijan el ámbito; cambiar identificadores, campos ocultos o filtros no concede otro ámbito. Una acción cruzada devuelve HTTP 403 y el lote no debe aplicarse parcialmente.

El Monitor Outbox requiere operación. El operador de Grado ve eventos de Grado e institucionales sin ámbito académico, no eventos de Posgrado ni Extensión. Un operador de varios ámbitos selecciona uno mediante `scope`; sólo `is_superuser` puede mezclarlos.

## Capas de implementación

- `apps/people/`: identidad, metamodelo de estados, vinculaciones, historial, permisos, TTL y Outbox persistente.
- `apps/portal/`: vistas autorizadas, servicios de consola, filtros ORM, exportación y plantillas compartidas.
- `apps/provisioning/`: reclamo de eventos, ejecución de sagas, continuaciones y recuperación.
- `apps/integrations/`: adaptadores de proveedores, contratos de resultados, espejos relacionales y sincronización.
- `apps/platform_mocks/`: proveedores locales y datos de prueba cuando están habilitados.

Las grillas comparten el mismo servicio tabular. Su presupuesto comprobado es de siete consultas de datos por página, con relaciones precargadas y cero consultas adicionales al renderizar. Ese número no representa todo el tráfico de una petición: sesión, autorización y petición separada del indicador se miden aparte. La grilla no consulta Graph ni Moodle al abrirse.
