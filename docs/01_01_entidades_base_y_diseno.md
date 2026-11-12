# Dominio, vinculaciones e integridad — v0.4.6

[Índice documental](README.md) · [Manual relacionado](manual/01_arquitectura_y_roles.md)

## Identidad y vinculación

`Identity` representa personas físicas u organizaciones; `PersonalProfile` guarda contacto. `Affiliation` vincula la identidad con un `ProfileTypeDefinition`, referencia académica, estado y vencimiento. `SagaStatusDefinition`, `SagaTransitionRule` y `CrossProfileConstraint` definen el grafo y sus restricciones. `DigitalAccount` conserva identificadores externos y observaciones; no es un almacén de contraseñas.

`ALUMNO` es el código de perfil de Grado y también un código de estado confirmado: interpretar cada campo en su contexto. `POSGRADO` y `EXTENSION` tienen perfiles separados. El metamodelo puede admitir otros perfiles institucionales, no todos visibles en las grillas académicas.

## Frontera transaccional

Crear o cambiar vinculaciones mediante `apps/people/services.py`. Allí se validan tipo/estado, reglas, restricciones y concurrencia; se escribe `AffiliationStatusHistory` y se emite `OutboxEvent` junto al cambio. Evitar `QuerySet.update()` en operaciones institucionales para saltarse historial y aprovisionamiento. La semilla controlada tiene una finalidad distinta.

La promoción por lote de Posgrado/Extensión bloquea las vinculaciones seleccionadas y confirma únicamente `PRE` a `ALUMNO`; los ya confirmados se omiten. No cambia por ello el grafo habitual de Grado ni autoriza otros ámbitos. Una identidad compartida no justifica mostrar sus vinculaciones ajenas.

## Relaciones consultables

Los espejos de Teams separan equipo, canal y membresía con FK; la identidad local de un invitado puede ser nula. La restricción `(workspace, upn)` evita duplicados. Los índices compuestos sirven a consultas analíticas sin recorrer listas JSON. Moodle conserva cursos en un espejo y `CatalogSyncState` registra ejecución y agenda por origen.

Las utilidades de UUID semánticos y huellas de contenido son componentes disponibles: no sustituyen automáticamente todas las claves primarias existentes. Consultar el modelo concreto antes de depender del formato de un identificador.

## Vencimiento

La regla `is_expiration=True` determina la salida por TTL. El barrido excluye estados sin regla y procesa un lote acotado; la baja remota conserva accesos respaldados por otras vinculaciones vigentes. Ver el capítulo 5 para la agenda y las condiciones de preservación.
