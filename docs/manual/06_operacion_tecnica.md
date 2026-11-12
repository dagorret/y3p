# 6. Comandos y verificación — v0.4.6

## Entorno verificado

Python **3.12.15**, Django **6.1.2**, PostgreSQL **16.15**. Usar el entorno `.venv` del proyecto y verificar versiones antes de operar. No imprimir `DATABASE_URL`, claves ni tokens para comprobar conectividad.

```bash
source .venv/bin/activate
python --version
python -m django --version
python manage.py shell -c "from django.db import connection; print(connection.vendor, connection.pg_version)"
python manage.py migrate
python manage.py makemigrations --check --dry-run
python manage.py showmigrations
python manage.py check
python manage.py test --noinput
```

El usuario PostgreSQL de pruebas debe poder crear y eliminar la base de pruebas; `--noinput` puede eliminar una base de pruebas residual, nunca debe apuntar a datos institucionales. No usar SQLite para certificar los bloqueos y consultas de esta versión. Respaldar antes de migrar una instalación real.

## Datos de desarrollo

Con `ENABLE_LOCAL_MOCKS=True` y migraciones aplicadas:

```bash
python manage.py seed_dev_students --count 60
```

La semilla es idempotente por identificadores estables y actualiza sus ejemplos al repetirla. Sólo debe utilizarse en desarrollo: reconfigura sus propios estados y observaciones, no representa una importación institucional.

| Datos propios de la semilla con `--count 60` | Cantidad |
|---|---:|
| Grado | 60 |
| Posgrado | 20 (10 PRE, 10 ALUMNO) |
| Extensión | 20 (10 PRE, 10 ALUMNO) |
| Equipos / canales | 3 / 6 |
| Miembros Teams | 61: 60 alumnos de Grado y un invitado sin identidad local |
| Cursos espejo Moodle | 3 |

Grado distribuye 30 verdes, 12 amarillos, 12 naranjas y 6 rojos; sus estados académicos son variados. Los equipos quedan con 31, 29 y 1 integrantes, e incluyen canales General estándar y Comisión 1 privada. Los cursos son 9040 Introducción a la Economía, 9041 Contabilidad I y 9042 Administración General. Estos conteos describen los registros de la semilla, no todos los datos que pueda contener una instalación. No ejecutar la semilla después de una prueba manual si se necesita conservar el estado modificado de sus ejemplos.

## Trabajadores y agenda

En terminales separadas, con Redis y PostgreSQL disponibles y destinos revisados:

```bash
python manage.py runserver 127.0.0.1:8000
celery -A config worker -l INFO -Q provisioning -n provisioning@%h
celery -A config worker -l INFO -Q moodle -n moodle@%h --concurrency=1
celery -A config beat -l INFO --schedule=/tmp/y3p-celerybeat-schedule
```

Mantener un único Beat por instalación y supervisar los procesos. El límite de tareas Moodle se aplica por trabajador; multiplicarlos requiere revisar el ritmo total. `runserver` sólo corresponde a desarrollo.

| Tarea de Beat | Periodicidad |
|---|---|
| Despachar Outbox | 60 segundos |
| Recuperar eventos trabados | 300 segundos |
| Revisar agendas de catálogos | 60 segundos; ejecuta sólo orígenes vencidos |
| Vencer vinculaciones TTL | Cada 15 minutos; lote de 500 |

## Intervenciones controladas

```bash
python manage.py process_expired_affiliations --dry-run --batch 500
```

La previsualización no modifica vinculaciones y presenta hasta un lote elegible. Omitir `--dry-run` aplica transiciones y puede crear eventos. El barrido manual y la tarea periódica utilizan el mismo dominio, con estrategias de recorrido distintas.

`sync_external_catalogs` es un servicio Python, **no** un management command. Para invocarlo sin inventar un comando inexistente:

```bash
python manage.py shell -c "from apps.integrations.catalogs import sync_external_catalogs; print(sync_external_catalogs())"
```

La alternativa operativa preferida es el botón de sincronización, que encola en Celery. La llamada directa sí consulta proveedores; revisar destinos y modo de ejecución antes de usarla.

## Variables y recursos

Revisar en `config/settings.py` los nombres leídos: `DATABASE_URL`, `CELERY_BROKER_URL`, `ENABLE_LOCAL_MOCKS`, `LUCY_DRY_RUN`, `M365_TENANT_ID`, `M365_CLIENT_ID`, `M365_CLIENT_SECRET`, `M365_A1_SKU_ID`, `MOODLE_BASE_URL`, `MOODLE_WSTOKEN`, `SIAL_BASE_URL`, `SIAL_USER`, `SIAL_PASSWORD`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `DEBUG`, `SECRET_KEY` y `ALLOWED_HOSTS`.

`ENABLE_LOCAL_MOCKS` instala los mocks y selecciona clientes locales en determinados servicios, como catálogos. No garantiza que todos los adaptadores apunten localmente. `LUCY_DRY_RUN` es una bandera distinta; simula escrituras y puede mantener lecturas remotas. La suite intercepta clientes explícitamente. No confiar en nombres heredados de `.env.example` que no coincidan con la configuración real.

`STATIC_URL` es `/static/`. Django descubre `apps/portal/static/` mediante `django.contrib.staticfiles`; `STATICFILES_DIRS` agrega la carpeta raíz sólo si existe. Comprobar con `python manage.py findstatic portal/js/student_grid.js`. El despliegue debe definir destino de recolección y entrega web antes de ejecutar `collectstatic` en producción.

Para validar los cinco comportamientos del JavaScript sin dependencias adicionales, usar Node disponible en desarrollo:

```bash
node tests/portal/js/test_frontend.js
```
