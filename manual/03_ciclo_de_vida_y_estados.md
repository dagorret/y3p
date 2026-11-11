# 03. Ciclo de vida y estados

[Índice general](00_indice_general.md) · [Anterior](02_entornos_de_trabajo.md) · [Siguiente](04_puesta_en_marcha_y_operacion.md)

## Identidad y vinculaciones

La identidad representa a la persona; una vinculación expresa su relación con la Facultad. El fin de una vinculación no elimina la identidad ni equivale necesariamente a perder todos los accesos.

```text
Identidad: una persona / un DNI
  |
  +--> Vinculación de grado: carrera A y estado actual
  +--> Vinculación docente: estado de servicio
  +--> Vinculación de extensión: curso y estado actual
  +--> Cuentas digitales: identificador y plataforma
```

## Recorrido de grado

El grafo inicial configurado para `ALUMNO` sigue este recorrido:

```text
PRE --> ASP --> INGRE --> ALUMNO --> TRAMITE_TITULO --> GRADUADO
 |       |        |
 v       v        v
BAJA_PRE BAJA_ASP BAJA_INGRE
```

| Estado | Significado | Vigencia inicial configurada |
|---|---|---|
| `PRE` | Preinscripción | 60 días |
| `ASP` | Aspirante | 180 días |
| `INGRE` | Ingresante | 365 días |
| `ALUMNO` | Alumno regular | Sin vencimiento automático por plazo |
| `TRAMITE_TITULO` | Trámite de egreso | 365 días; al vencer pasa a graduado |
| `GRADUADO`, `BAJA_*` | Estados terminales | Vinculación inactiva |

Los plazos se computan al entrar al estado. El vencimiento requiere ejecutar el proceso de barrido; no basta con que transcurra el tiempo. Las reglas son configurables y deben revisarse si la institución modifica sus criterios.

La configuración inicial solicita aprovisionamiento al entrar a `INGRE` o `ALUMNO`, y suspensión al entrar a `GRADUADO`. No todos los estados de baja emiten una acción externa. Para docentes, `SI` aprovisiona y `NO` suspende. Posgrado y extensión tienen sus propios estados, sin el recorrido completo de grado.

> [!NOTE]
> Antes de retirar una licencia o deshabilitar una cuenta compartida, el orquestador consulta las otras vinculaciones activas de la persona. En v0.2.0 la acción `TERMINATE` sigue la política de suspensión y no elimina remotamente la cuenta.

## Identificador semántico UUIDv8 — referencia para TI

La utilidad `generate_institutional_uuid` genera el mismo UUID para la misma combinación de DNI, año, origen y tipo. Su decodificación no consulta la base.

```text
fce00000-0000-8YYY-90OT-DDDDDDDDDDDD
                 |  ||       |
                 |  ||       +-- DNI decimal convertido a 12 hexadecimales
                 |  |+---------- Tipo: un dígito hexadecimal
                 |  +----------- Origen: un dígito hexadecimal
                 +-------------- Año: tres dígitos hexadecimales tras versión 8
```

El cuarto grupo usa `90` seguido de origen y tipo: para ambos códigos en 1, resulta `9011`. Ejemplo: DNI `12345678`, año `2026` → `fce00000-0000-87ea-9011-000000bc614e`.

El año admite 0–4095, origen y tipo 0–15, y el DNI ocupa 48 bits. La decodificación devuelve el DNI decimal sin ceros iniciales. Cambiar año, origen o tipo cambia el identificador. Este UUID contiene datos recuperables: no es una contraseña ni anonimiza el DNI.

## DigitalAccount y huella de contenido

`DigitalAccount` permite asociar cuentas `TEAMS`, `GOOGLE`, `MOODLE`, `PERSONAL` u `OTRO` a una identidad. La combinación plataforma/identificador de cuenta es única en toda la tabla. Una identidad puede tener varias cuentas, incluso en una misma plataforma con identificadores distintos.

Cada cuenta registra actividad, fechas de creación/actualización y una huella opcional `content_hash`. La función `calculate_content_hash` calcula SHA-256 sobre JSON con claves ordenadas y textos sin espacios exteriores. Conserva espacios internos y orden de listas; no modifica los datos recibidos.

> [!NOTE]
> La huella sirve para comparar contenido cuando un proceso la utiliza. No se calcula al guardar DigitalAccount ni evita cambios automáticamente. Tampoco existen todavía adaptador Google, sincronización automática de DigitalAccount, ni sustitución de `Identity.institutional_upn` por este modelo. Los UUID existentes de eventos siguen siendo independientes del generador semántico.
