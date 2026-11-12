"""Configuración Django del proyecto modular MT^3/Y3P, FCE - UNRC.

El archivo inicial fue generado con Django 6.1.2 y adaptado a PostgreSQL,
portales y orquestación. Referencia de parámetros:
https://docs.djangoproject.com/en/6.1/ref/settings/.
"""

from pathlib import Path
from celery.schedules import crontab
import environ
from .unfold import UNFOLD

# Directorio base del proyecto (donde reside manage.py y el archivo .env)
BASE_DIR = Path(__file__).resolve().parent.parent

# Nombre de la aplicación institucional
APP_NAME = "Mt^3/Y3P"

# -----------------------------------------------------------------------------
# 1. CARGA DE VARIABLES DE ENTORNO (.env)
# -----------------------------------------------------------------------------
env = environ.Env(
    DEBUG=(bool, False),
    ENABLE_LOCAL_MOCKS=(bool, False),
)

# Lectura del archivo .env si existe en la raíz
environ.Env.read_env(BASE_DIR / ".env")


# -----------------------------------------------------------------------------
# 2. SEGURIDAD Y ENTORNO DE EJECUCIÓN
# -----------------------------------------------------------------------------
# Clave secreta leída del entorno (con fallback temporal para entorno local)
SECRET_KEY = env(
    "SECRET_KEY",
    default="django-insecure-3)-l^^_zg8ct+0vj^$-48em-^hwe51w2gi9yg(_g^9vw9gl-&v",
)

# Modo depuración controlado por variable de entorno
DEBUG = env("DEBUG")

# Hosts permitidos
ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=["*"] if DEBUG else ["localhost", "127.0.0.1"],
)

# Bandera para montar o desacoplar mocks de desarrollo (UTI, Graph, Moodle)
ENABLE_LOCAL_MOCKS = env("ENABLE_LOCAL_MOCKS")


# -----------------------------------------------------------------------------
# 3. DEFINICIÓN DE APLICACIONES (INSTALLED_APPS)
# -----------------------------------------------------------------------------
INSTALLED_APPS = [
    # Apps del núcleo de Django
    "unfold",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # El núcleo de identidades y vinculaciones forma parte de todos los entornos.
    "apps.people",
    "apps.integrations",
    "apps.provisioning",
    "apps.portal",

    # Módulo de simulación local (solo se monta si ENABLE_LOCAL_MOCKS=True)
    *(["apps.platform_mocks"] if ENABLE_LOCAL_MOCKS else []),
]

# -----------------------------------------------------------------------------
# 4. MIDDLEWARE
# -----------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"


# -----------------------------------------------------------------------------
# 5. PLANTILLAS Y PRESENTACIÓN
# -----------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.portal.context_processors.administration_permissions",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# -----------------------------------------------------------------------------
# 6. BASE DE DATOS (PostgreSQL vía psycopg 3 / Fallback a SQLite)
# -----------------------------------------------------------------------------
# Lee DATABASE_URL desde el archivo .env
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}


# -----------------------------------------------------------------------------
# 7. VALIDACIÓN DE CONTRASEÑAS
# -----------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# -----------------------------------------------------------------------------
# 8. INTERNACIONALIZACIÓN Y ZONA HORARIA (Argentina / Córdoba)
# -----------------------------------------------------------------------------
LANGUAGE_CODE = "es-ar"

TIME_ZONE = "America/Argentina/Cordoba"

USE_I18N = True

USE_TZ = True


# -----------------------------------------------------------------------------
# 9. RECURSOS ESTÁTICOS
# -----------------------------------------------------------------------------
#FPORTALSTATIC01-12001: La ruta absoluta conserva los recursos en todas las rutas anidadas.
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []


# -----------------------------------------------------------------------------
# 10. CORREO ELECTRÓNICO (Django 6.1 Mailers)
# -----------------------------------------------------------------------------
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.console.EmailBackend",
    },
}

# ----------------------------------------------------------------------------
# 11. Aprovisionamiento asíncrono y clientes Lucy
# ----------------------------------------------------------------------------
LUCY_DRY_RUN = env.bool("LUCY_DRY_RUN", default=False)
LUCY_M365_TENANT_ID = env("M365_TENANT_ID", default="")
LUCY_M365_CLIENT_ID = env("M365_CLIENT_ID", default="")
LUCY_M365_CLIENT_SECRET = env("M365_CLIENT_SECRET", default="")
LUCY_M365_SKU_ID = env("M365_A1_SKU_ID", default="")
LUCY_MOODLE_BASE_URL = env("MOODLE_BASE_URL", default="")
LUCY_MOODLE_TOKEN = env("MOODLE_WSTOKEN", default="")
LUCY_SIAL_BASE_URL = env('SIAL_BASE_URL', default='')
LUCY_SIAL_USERNAME = env('SIAL_USER', default='')
LUCY_SIAL_PASSWORD = env('SIAL_PASSWORD', default='')
LUCY_SMTP_HOST = env("SMTP_HOST", default="")
LUCY_SMTP_PORT = env.int("SMTP_PORT", default=587)
LUCY_SMTP_USERNAME = env("SMTP_USERNAME", default="")
LUCY_SMTP_PASSWORD = env("SMTP_PASSWORD", default="")
LUCY_SMTP_FROM_EMAIL = env("SMTP_FROM_EMAIL", default="")

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_TASK_ROUTES = {
    "provisioning.dispatch_outbox": {"queue": "provisioning"},
    "provisioning.run_saga": {"queue": "provisioning"},
    "provisioning.moodle_step": {"queue": "moodle"},
    "provisioning.sweep_stuck_events": {"queue": "provisioning"},
}
CELERY_BEAT_SCHEDULE = {
    "provisioning-dispatch-outbox-every-minute": {
        "task": "provisioning.dispatch_outbox",
        "schedule": 60.0,
    },
    "provisioning-sweep-stuck-events-every-five-minutes": {
        "task": "provisioning.sweep_stuck_events",
        "schedule": 300.0,
    },
}

#FCATALOGSCHEDULE01-9201: Beat respeta la agenda editable de cada origen en PostgreSQL.
CELERY_BEAT_SCHEDULE['catalogs-check-every-minute'] = {
    'task': 'integrations.sync_due_catalogs', 'schedule': 60.0,
    'options': {'queue': 'provisioning'},
}


#FTTLSCHEDULE01-10305: El barrido usa ventanas de quince minutos y lotes acotados independientes.
CELERY_BEAT_SCHEDULE['affiliations-expire-every-fifteen-minutes'] = {
    'task': 'people.process_expired_affiliations', 'schedule': crontab(minute='*/15'),
    'kwargs': {'limit': 500}, 'options': {'queue': 'provisioning'},
}
