# Y3P / MT³ — Gestión y aprovisionamiento académico

Facultad de Ciencias Económicas, Universidad Nacional de Río Cuarto. Referencia documental: **v0.4.6**.

Y3P administra identidades y vinculaciones de Grado, Posgrado y Extensión. Sus consolas aplican permisos por ámbito, seguimiento local de salud, fórmulas guardadas, exportación y comunicaciones. El aprovisionamiento se registra en un Outbox transaccional y se ejecuta con Celery sobre Microsoft, Moodle y correo mediante adaptadores.

## Documentación vigente

Leer el [Manual de Usuario y Operación](docs/README.md): roles y navegación, flujos diarios, snippets, recuperación Outbox, espejos y TTL, comandos, organización del frontend y preparación de producción. Las rutas anteriores en `manual/` redirigen a los capítulos actuales.

## Entorno y verificación

Python **3.12.15**, Django **6.1.2**, PostgreSQL **16.15** y Redis para Celery. Con el entorno configurado y los destinos revisados:

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py check
python manage.py test --noinput
```

La [guía técnica](docs/manual/06_operacion_tecnica.md) explica trabajadores, Beat, semilla y comandos. La [auditoría v0.4.6](docs/verificacion/auditoria_codigo_v046.txt) contiene métricas y validación. La suite utiliza PostgreSQL y proveedores interceptados; no certifica un tenant remoto.

## Mapa del código

| Carpeta | Responsabilidad |
|---|---|
| `apps/people/` | Identidad, estados, vinculaciones, permisos, TTL y Outbox |
| `apps/portal/` | Consolas, filtros ORM, exportación, plantillas y JavaScript propio |
| `apps/provisioning/` | Despacho y ejecución de sagas asíncronas |
| `apps/integrations/` | Adaptadores, espejos relacionales y sincronización |
| `apps/platform_mocks/` | Proveedores y datos locales de desarrollo |
| `tests/` | Regresión funcional, integración y certificación E2E |
| `docs/` | Manual, arquitectura y evidencias |

La capa de Seguridad y Producción permanece pendiente: consultar su [lista de trabajo](docs/manual/08_produccion_pendiente.md) antes de habilitar accesos institucionales.
