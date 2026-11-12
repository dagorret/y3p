# Manual de Usuario y Operación — Y3P v0.4.6

Edición: 12 de noviembre de 2026. Entorno de referencia: Python 3.12.15, Django 6.1.2 y PostgreSQL 16.15. El manual describe el código de este release; las evidencias históricas conservan sus fechas y versiones.

## Índice de lectura

1. [Arquitectura tripartita y permisos](manual/01_arquitectura_y_roles.md).
2. [Operación diaria de las consolas](manual/02_operacion_diaria.md).
3. [Vistas guardadas y fórmulas](manual/03_vistas_y_formulas.md).
4. [Outbox transaccional y recuperación](manual/04_outbox_y_recuperacion.md).
5. [Espejos relacionales y automatización](manual/05_espejos_y_vencimientos.md).
6. [Comandos y verificación](manual/06_operacion_tecnica.md).
7. [Organización del frontend y mantenimiento](manual/07_frontend_y_mantenimiento.md).
8. [Preparación de Seguridad y Producción](manual/08_produccion_pendiente.md).

Operadores: capítulos 1 a 5. TI: manual completo, especialmente 6 y 8. Mantenedores: capítulos 1, 4, 5 y 7 más referencias técnicas.

## Referencias técnicas actualizadas

- [Entorno e inicialización](01_inicializacion_entorno.md).
- [Modelo de dominio](01_01_entidades_base_y_diseno.md).
- [Contratos de integración](01_02_clientes_integracion.md).
- [Implementación de adaptadores](01_02_Implementación_Integraciones_Adaptadores.md).
- [Patrón adaptador e idempotencia](1_00_adaptador_api.md).
- [Outbox y orquestador](01_03_outbox_y_orquestador.md).
- [Seguridad y límites de la certificación](00_principios_seguridad.md).

## Evidencia y alcance

La [auditoría de código v0.4.6](verificacion/auditoria_codigo_v046.txt) presenta inventario, ranking completo y validaciones. La salida íntegra de la suite se conserva en `verificacion/pruebas_v046_postgresql.txt`. La [certificación anterior](verificacion/certificacion_pre_seguridad_v045.txt) conserva el recorrido E2E pre-seguridad; no reemplaza la aprobación productiva pendiente.

Este manual reemplaza las instrucciones de v0.2.0 que residían en `manual/`. Allí se mantienen enlaces de compatibilidad. No publicar datos reales ni secretos en ejemplos operativos.
