# Implementación y mantenimiento de adaptadores — v0.4.6

[Índice documental](README.md) · [Manual relacionado](manual/07_frontend_y_mantenimiento.md)

## Cómo ampliar una integración

1. Definir o ampliar el contrato en `services/base.py` y los resultados tipados en `dataclasses.py`.
2. Implementar el cliente del proveedor y traducir fallos a las excepciones de integración; conservar identificadores externos estables.
3. Registrar la construcción en fábricas y distinguir explícitamente simulación de éxito remoto.
4. Integrar el paso en la saga o servicio de catálogos; actualizar observaciones sólo después de confirmar resultados.
5. Añadir pruebas de reintento, paginación, duplicados, fallo parcial y ausencia de secretos en persistencia y registros.

Los clientes no importan modelos para decidir política de negocio. Las transacciones locales no deben mantenerse abiertas durante una enumeración remota extensa. El sincronizador obtiene la fotografía completa y luego confirma reemplazos por origen, con concesión de ejecución.

## Agregar una plataforma no es sólo un dato

La existencia de una opción en el metamodelo no implementa autenticación, licencias, grupos ni revocación en un proveedor nuevo. Deben existir contrato, adaptador, fábrica, integración con saga y pruebas. Mantener el ámbito y la política de accesos compartidos en el dominio.

Los documentos anteriores de diseño fueron sustituidos por esta referencia de v0.4.6 y el manual enlazado. Las evidencias en `verificacion/` conservan su carácter histórico.
