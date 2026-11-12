# Guía de Despliegue en Producción: Docker Compose + Apache 2.4 (GeoIP2)

> **Proyecto:** Mt³ / Y3P — Facultad de Ciencias Económicas (UNRC)  
> **Sistema Operativo Host:** Ubuntu Server 26.04 LTS  
> **Arquitectura:** Proxy Inverso Apache 2.4 (TLS + MaxMind GeoIP2) + Stack Docker Desacoplado

---

## 1. Arquitectura de Red y Contenedores

El servidor **Ubuntu 26.04** ejecuta **Apache 2.4** directamente sobre el sistema anfitrión como único punto de entrada público (puertos `80` y `443`). Apache gestiona los certificados TLS/SSL, filtra las conexiones por geolocalización (**GeoIP2**) y redirige únicamente el tráfico autorizado hacia el contenedor `web` (Gunicorn), que escucha de forma aislada en `127.0.0.1:8000`.

```text
Internet / Red UNRC (170.210.0.0/16)
        │
        ▼ (Puertos 80 / 443)
┌───────────────────────────────────────────────────────────┐
│  Ubuntu 26.04 Host                                        │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Apache 2.4 (SSL + libapache2-mod-maxminddb)        │  │
│  │  • Whitelist: Argentina (AR) + UNRC (170.210.0.0/16)│  │
│  │  • Bloqueo inmediato 403 al tráfico extranjero       │  │
│  └──────────────────────────┬──────────────────────────┘  │
│                             │ ProxyPass 127.0.0.1:8000    │
│  ┌──────────────────────────▼──────────────────────────┐  │
│  │  Docker Network: y3p_internal                       │  │
│  │  • web (Django 6 + Gunicorn)                        │  │
│  │  • worker_general (Celery: default, graph, mail)    │  │
│  │  • worker_moodle (Celery: moodle_tasks, concur.=1)  │  │
│  │  • beat (Celery Beat: DatabaseScheduler)            │  │
│  │  • db (PostgreSQL 16 Alpine)                        │  │
│  │  • redis (Redis 7 Alpine)                           │  │
│  └─────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────┘
```

---

## 2. Archivos de Contenedores (`deploy/`)

### 2.1. `deploy/Dockerfile`

Construye la imagen de aplicación utilizando Python 3.12, `uv` para resolución determinista desde `uv.lock` y ejecución bajo un usuario sin privilegios (`y3puser`).

```dockerfile
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependencias del sistema para psycopg y healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar uv desde el binario oficial
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Instalar dependencias primero (aprovecha caché de capas de Docker)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copiar el código fuente de Y3P
COPY . .
RUN uv sync --frozen --no-dev

# Crear directorios de estáticos/media y usuario no-root
RUN mkdir -p /app/staticfiles /app/media && \
    useradd -m -u 1000 y3puser && \
    chown -R y3puser:y3puser /app

USER y3puser

EXPOSE 8000

CMD ["uv", "run", "gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
```

### 2.2. `deploy/docker-compose.yml`

Orquesta los 6 servicios en una red interna aislada (`y3p_internal`). Solo `web` expone su puerto hacia `127.0.0.1:8000`.

```yaml
services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-y3p_prod}
      POSTGRES_USER: ${POSTGRES_USER:-y3p_user}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Definir POSTGRES_PASSWORD en .env}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-y3p_user} -d ${POSTGRES_DB:-y3p_prod}"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - y3p_internal

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy noeviction
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - y3p_internal

  web:
    build:
      context: ..
      dockerfile: deploy/Dockerfile
    restart: unless-stopped
    env_file:
      - ../.env
    environment:
      DATABASE_URL: postgres://${POSTGRES_USER:-y3p_user}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB:-y3p_prod}
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/1
    ports:
      # Expuesto únicamente en loopback para el proxy inverso Apache
      - "127.0.0.1:8000:8000"
    volumes:
      - static_volume:/app/staticfiles
      - media_volume:/app/media
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health/"]
      interval: 15s
      timeout: 5s
      retries: 3
    networks:
      - y3p_internal

  worker_general:
    build:
      context: ..
      dockerfile: deploy/Dockerfile
    restart: unless-stopped
    command: uv run celery -A config worker -Q default,graph,mail -l info --concurrency=4
    env_file:
      - ../.env
    environment:
      DATABASE_URL: postgres://${POSTGRES_USER:-y3p_user}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB:-y3p_prod}
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/1
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    networks:
      - y3p_internal

  worker_moodle:
    build:
      context: ..
      dockerfile: deploy/Dockerfile
    restart: unless-stopped
    command: uv run celery -A config worker -Q moodle_tasks -l info --concurrency=1
    env_file:
      - ../.env
    environment:
      DATABASE_URL: postgres://${POSTGRES_USER:-y3p_user}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB:-y3p_prod}
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/1
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    networks:
      - y3p_internal

  beat:
    build:
      context: ..
      dockerfile: deploy/Dockerfile
    restart: unless-stopped
    command: uv run celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
    env_file:
      - ../.env
    environment:
      DATABASE_URL: postgres://${POSTGRES_USER:-y3p_user}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB:-y3p_prod}
      CELERY_BROKER_URL: redis://redis:6379/0
      CELERY_RESULT_BACKEND: redis://redis:6379/1
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    networks:
      - y3p_internal

volumes:
  postgres_data:
  redis_data:
  static_volume:
  media_volume:

networks:
  y3p_internal:
    driver: bridge
```

---

## 3. Configuración de Apache 2.4 + GeoIP2 en Ubuntu 26.04

### 3.1. Instalación de paquetes y habilitación de módulos

```bash
sudo apt update
sudo apt install -y apache2 libapache2-mod-maxminddb mmdb-bin
sudo a2enmod proxy proxy_http headers rewrite ssl maxminddb
sudo systemctl restart apache2
```

*Verificación:* Ejecutá `apache2ctl -M | grep -E "proxy|maxminddb"` y confirmá que se listan `proxy_module`, `proxy_http_module` y `maxminddb_module`.

### 3.2. Instalación de la base MaxMind GeoLite2

1. Descargá `GeoLite2-Country.mmdb` desde tu cuenta gratuita de MaxMind.
2. Ubicala en `/usr/share/GeoIP/GeoLite2-Country.mmdb` y verificá que funcione con una IP de la UNRC:

```bash
sudo mkdir -p /usr/share/GeoIP
sudo cp GeoLite2-Country.mmdb /usr/share/GeoIP/
sudo chmod 644 /usr/share/GeoIP/GeoLite2-Country.mmdb

# Prueba de consulta local
mmdblookup --file /usr/share/GeoIP/GeoLite2-Country.mmdb --ip 170.210.1.1 country iso_code
```

### 3.3. Configuración del VirtualHost (`/etc/apache2/sites-available/y3p.conf`)

Creá el archivo `/etc/apache2/sites-available/y3p.conf` con el siguiente contenido:

```apache
<VirtualHost *:80>
    ServerName y3p.eco.unrc.edu.ar
    RewriteEngine On
    RewriteRule ^ https://%{SERVER_NAME}%{REQUEST_URI} [END,NE,R=301]
</VirtualHost>

<IfModule mod_ssl.c>
<VirtualHost *:443>
    ServerName y3p.eco.unrc.edu.ar

    SSLEngine on
    SSLCertificateFile /etc/ssl/certs/y3p.eco.unrc.edu.ar.crt
    SSLCertificateKeyFile /etc/ssl/private/y3p.eco.unrc.edu.ar.key

    # --- Base de datos MaxMind GeoIP2 ---
    MaxMindDBEnable On
    MaxMindDBFile COUNTRY_DB /usr/share/GeoIP/GeoLite2-Country.mmdb
    MaxMindDBEnv MM_COUNTRY_CODE COUNTRY_DB/country/iso_code

    # --- Control de Acceso Geográfico + Whitelist Institucional ---
    <Location />
        <RequireAny>
            # 1. Loopback y redes privadas RFC1918
            Require ip 127.0.0.1 ::1 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16
            # 2. Rango oficial UNRC
            Require ip 170.210.0.0/16
            # 3. Conexiones geolocalizadas dentro de Argentina
            Require env MM_COUNTRY_CODE=AR
        </RequireAny>
    </Location>

    # --- Cabeceras de Seguridad HTTP ---
    Header always set X-Frame-Options "DENY"
    Header always set X-Content-Type-Options "nosniff"
    Header always set Referrer-Policy "strict-origin-when-cross-origin"
    Header always set Strict-Transport-Security "max-age=31536000; includeSubDomains"

    # --- Reverse Proxy hacia Gunicorn (Docker 127.0.0.1:8000) ---
    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "https"
    RequestHeader set X-Forwarded-Port "443"

    ProxyPass / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/

    ErrorLog ${APACHE_LOG_DIR}/y3p_error.log
    CustomLog ${APACHE_LOG_DIR}/y3p_access.log combined
</VirtualHost>
</IfModule>
```

---

## 4. Puesta en Marcha y Verificación

```bash
# 1. Activar sitio en Apache y recargar configuración
sudo a2ensite y3p.conf
sudo apache2ctl configtest
sudo systemctl reload apache2

# 2. Construir y levantar el stack completo en segundo plano
docker compose -f deploy/docker-compose.yml up -d --build

# 3. Aplicar migraciones y recolectar archivos estáticos
docker compose -f deploy/docker-compose.yml exec web uv run python manage.py migrate
docker compose -f deploy/docker-compose.yml exec web uv run python manage.py collectstatic --noinput
```

### Checklist de Validación Post-Despliegue

1. **Estado de contenedores:** Ejecutar `docker compose -f deploy/docker-compose.yml ps` y comprobar que los 6 servicios estén en estado `Up` o `healthy`.
2. **Aislamiento de puertos:** Ejecutar `ss -tulpn | grep 8000` y verificar que Gunicorn escuche únicamente en `127.0.0.1:8000` (nunca en `0.0.0.0:8000`).
3. **Filtro GeoIP2:**
   * Desde red Argentina o intranet UNRC (`170.210.x.x`): `curl -I https://y3p.eco.unrc.edu.ar/health/` debe devolver `HTTP/2 200`.
   * Desde una VPN fuera de Argentina: la misma petición debe ser rechazada en borde por Apache con `HTTP/2 403 Forbidden`.
4. **Archivos estáticos:** Verificar que `whitenoise` esté activo en `MIDDLEWARE` de `config/settings.py` para que Gunicorn sirva `/static/` directamente a través de Apache.