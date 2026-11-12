# Seguridad y límites de la certificación — v0.4.6

[Índice documental](README.md) · [Manual relacionado](manual/08_produccion_pendiente.md)

## Controles existentes

El servidor valida capacidades por ámbito en vistas y servicios; CSRF protege los POST. El parser usa una lista blanca de condiciones ORM y la exportación protege celdas de texto. El Outbox conserva intenciones y trazabilidad; el rearme exige autorización crítica. Las pruebas E2E interceptan proveedores y comprueban ausencia de secretos canarios en eventos y registros incluso al fallar SMTP.

## Límites efectivos

No confundir mocks con desconexión de red: `ENABLE_LOCAL_MOCKS` y `LUCY_DRY_RUN` son controles diferentes y algunos adaptadores pueden efectuar lecturas remotas. Tampoco confundir simulación con operación confirmada: `SIMULATED` no acredita aprovisionamiento real.

La configuración local todavía presenta deuda de despliegue, incluido `mail.E001`, avisos de HTTPS/cookies/DEBUG y `.env` bajo seguimiento. La lista de producción incluye Nginx IP/GeoIP2, 2FA TOTP, secretos reales, entrega de estáticos, supervisión y recuperación. No hay una certificación de producción ni de un tenant remoto implícita en la suite verde.
