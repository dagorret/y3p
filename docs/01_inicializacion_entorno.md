# Entorno e inicialización — v0.4.6

[Índice documental](README.md) · [Manual relacionado](manual/06_operacion_tecnica.md)

## Stack de referencia

| Componente | Versión verificada |
|---|---|
| Python | 3.12.15 |
| Django | 6.1.2 |
| PostgreSQL | 16.15 |

La configuración efectiva proviene de `config/settings.py` y del entorno de cada instalación. Comprobar versiones sin imprimir secretos. La certificación utiliza PostgreSQL: SQLite no reproduce el bloqueo de filas, JSON e índices utilizados por la aplicación.

## Preparación

Usar `.venv`, instalar las dependencias según el archivo de bloqueo, revisar destinos y aplicar `manage.py migrate`. El capítulo técnico del manual contiene los comandos de prueba, semilla, previsualización TTL y sincronización. No sobrescribir una configuración `.env` existente ni copiar las antiguas credenciales de ejemplo.

La web y los trabajadores se ejecutan por separado. Se requieren Redis, las colas `provisioning` y `moodle`, y una sola instancia de Beat. Las migraciones de datos crean el metamodelo y grupos; las cuentas de usuario no se presumen creadas por la semilla académica. Crear el superusuario por el diálogo `manage.py createsuperuser` cuando corresponda.
