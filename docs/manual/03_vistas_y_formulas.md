# 3. Vistas guardadas y fórmulas — v0.4.6

En cada consola, el selector «Vistas Guardadas (Snippets)» está debajo de la grilla, junto a la paginación. Aplicar una vista actualiza resultados y filtros mediante HTMX, conservando la ruta del ámbito.

La herramienta lateral «Vistas y Snippets» abre `/dash/administracion/vistas/`, `/dash/postgrado/vistas/` o `/dash/extension/vistas/`. Allí están el editor, la ayuda, la vista previa y la gestión. La consulta requiere lectura; guardar requiere operación. Una vista privada sólo es visible para su creador. Una compartida se muestra al equipo del mismo ámbito; compartir no la convierte en una consulta institucional transversal. El creador o administrador del ámbito puede eliminarla.

## Gramática disponible

Usar condiciones `clave:valor` unidas por `AND`. Se admiten comillas para valores con espacios. No se admiten SQL, nombres arbitrarios de campos, `OR` ni evaluación de código. La fórmula tiene un límite de 2.000 caracteres y 30 condiciones. Una expresión inválida muestra un mensaje controlado; no debería producir HTTP 500.

| Clave | Significado | Ejemplo |
|---|---|---|
| `carrera` | Código o parte del nombre/referencia de carrera | `carrera:CONTADOR` |
| `saga` | Estado de vinculación del ámbito | `saga:PRE` |
| `salud` | Diagnóstico local | `salud:ROJO` |
| `dni` | Prefijo del identificador documental | `dni:42100` |
| `licencia` | Licencia observada | `licencia:SI` o `licencia:NO` |
| `moodle` | Matrícula activa observada | `moodle:SI` o `moodle:NO` |
| `vence` | TTL ya vencido o ventana próxima en días | `vence:vencido` o `vence:<30d` |

Ejemplos:

```text
carrera:CONTADOR AND salud:ROJO AND vence:vencido
saga:PRE AND licencia:NO
saga:ALUMNO AND moodle:SI AND vence:<30d
salud:AMARILLO AND dni:42100
```

El parser traduce una lista blanca a objetos `Q` parametrizados del ORM. Las condiciones académicas se aplican a la misma vinculación; el ámbito lo impone el servidor. Los filtros rápidos complementan la fórmula, por lo que una combinación contradictoria puede devolver cero filas. La vista guardada incluye expresión y parámetros complementarios; revisar ambos al editarla.
