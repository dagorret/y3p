# 04. Puesta en marcha y operación técnica

[Índice general](00_indice_general.md) · [Anterior](03_ciclo_de_vida_y_estados.md) · [Siguiente](05_contingencias_y_faq.md)

## Alcance y prerrequisitos

Este procedimiento es para desarrollo local. Requiere Docker disponible, `uv`, acceso al repositorio y una configuración `.env` revisada. El proyecto no incluye un archivo Docker Compose: los siguientes nombres y comandos son una propuesta reproducible para una instalación nueva, no una descripción de contenedores existentes.

Antes de crear servicios, ejecutar `docker ps -a` y comprobar si PostgreSQL y Redis ya ocupan los puertos 5432 y 6379. Si existen contenedores institucionales, usar sus nombres y configuración, sin crear duplicados.

## PostgreSQL y Redis en Docker

Ejemplo para un entorno local nuevo; los volúmenes conservan los datos al detener el contenedor:

```bash
docker volume create y3p_pgdata
docker volume create y3p_redisdata
docker run -d --name y3p-postgres --restart unless-stopped \
  -p 127.0.0.1:5432:5432 -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=root -e POSTGRES_DB=postgres \
  -v y3p_pgdata:/var/lib/postgresql/data postgres:16
docker run -d --name y3p-redis --restart unless-stopped \
  -p 127.0.0.1:6379:6379 -v y3p_redisdata:/data redis:7 \
  redis-server --appendonly yes
```

En arranques posteriores, para estos nombres:

```bash
docker start y3p-postgres y3p-redis
docker exec y3p-postgres pg_isready -U postgres -d postgres
docker exec y3p-redis redis-cli ping
```

Esperar `accepting connections` y `PONG`. No eliminar volúmenes para resolver una caída.

## Entorno de aplicación

```bash
cd ~/work/y3p
uv sync --locked
```

Si falta `.env`, copiar `.env.example` una sola vez y completar sus valores. No sobrescribir la configuración de una instalación existente. Revisar las variables efectivamente leídas en [settings.py](../config/settings.py):

| Variables | Finalidad |
|---|---|
| `DATABASE_URL` | Para el ejemplo: `postgres://postgres:root@127.0.0.1:5432/postgres` |
| `CELERY_BROKER_URL` | Redis; ejemplo `redis://localhost:6379/0` |
| `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS` | Configuración del entorno web |
| `LUCY_DRY_RUN` | `True` para simular escrituras de los adaptadores |
| `ENABLE_LOCAL_MOCKS` | Instala la aplicación de mocks; no activa por sí sola el dry-run |
| `M365_TENANT_ID`, `M365_CLIENT_ID`, `M365_CLIENT_SECRET`, `M365_A1_SKU_ID` | Credenciales y licencia Microsoft |
| `MOODLE_BASE_URL`, `MOODLE_WSTOKEN` | Moodle |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL` | Cliente de correo de aprovisionamiento |

> [!WARNING]
> `.env.example` contiene nombres heredados como `CRM_DRY_RUN`, `AZURE_*`, `MOODLE_REST_TOKEN` y `SMTP_USER`; no sustituyen las variables de Lucy de esta tabla. `LUCY_DRY_RUN` vale False por defecto. La presencia de mocks no garantiza que las integraciones apunten a servicios locales. Revisar destinos antes de iniciar los trabajadores; incluso en dry-run algunas consultas de lectura pueden usar la red.

Preparar la base y comprobar la aplicación:

```bash
uv run python manage.py migrate
uv run python manage.py showmigrations people
uv run python manage.py check
```

En v0.2.0 deben figurar aplicadas las migraciones de `people` hasta `0007_digitalaccount`. Respaldar la base antes de migrar una instalación con datos institucionales.

## Cuenta administrativa inicial

En el entorno local preparado durante el cierre de versión se creó `admin`, correo `admin@eco.unrc.edu.ar`, con clave inicial `admin1234`. No es una cuenta creada por migraciones ni una credencial garantizada para otras instalaciones.

Para una instalación nueva sin cuenta, crearla mediante el diálogo:

```bash
uv run python manage.py createsuperuser --username admin --email admin@eco.unrc.edu.ar
```

Para cambiar la clave de una cuenta existente:

```bash
uv run python manage.py changepassword admin
```

> [!WARNING]
> `admin1234` y la clave PostgreSQL `root` son valores exclusivos del ejemplo local. Sustituirlos antes de habilitar accesos compartidos. Mantener `.env` y sus secretos fuera del repositorio.

## Aplicación y procesos en segundo plano

Ejecutar cada comando en una terminal distinta, desde la raíz del proyecto:

```bash
uv run python manage.py runserver 127.0.0.1:8000
```

```bash
uv run celery -A config worker -l INFO -Q provisioning -n provisioning@%h
```

```bash
uv run celery -A config worker -l INFO -Q moodle -n moodle@%h --concurrency=1
```

```bash
uv run celery -A config beat -l INFO --schedule=/tmp/y3p-celerybeat-schedule
```

Mantener un único Beat por instalación y un único trabajador Moodle para el límite configurado de `150/m`, que se aplica por trabajador. Beat despacha pendientes cada 60 segundos y ejecuta recuperación cada 300 segundos. Su archivo de agenda es estado local; no contiene el Outbox, que reside en PostgreSQL.

El vencimiento de vinculaciones es un proceso distinto y no está incluido en esa agenda. TI debe programarlo según la política institucional, revisando primero:

```bash
uv run python manage.py process_expired_affiliations --dry-run --batch 500
```

Sin `--dry-run` aplica transiciones y puede generar trabajo externo. La previsualización muestra como máximo un lote.

Abrir `http://127.0.0.1:8000/admin/` o las rutas de portal del capítulo 2. Verificar que cada identidad a aprovisionar tenga un UPN institucional: el portal no lo asigna y el evento conserva una copia de los datos al emitirse. Si falta, escalar a TI; corregir solo la identidad después de emitir el evento no actualiza esa copia.

Para detener el ejemplo local, finalizar Django, Beat y trabajadores con Ctrl+C y luego usar `docker stop y3p-redis y3p-postgres`. Un despliegue institucional requiere supervisión de procesos, servidor web adecuado y controles de acceso; `runserver` es para desarrollo.
