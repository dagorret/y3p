# 🏛️ Mt^3 / Y3P — Sistema de Gestión y Aprovisionamiento Académico
> **Facultad de Ciencias Económicas — Universidad Nacional de Río Cuarto (UNRC)**  
> Plataforma modular y resiliente para el aprovisionamiento de identidades, sincronización académica y auditoría de cuentas.

---

## 📌 ¿Qué es este proyecto?

**Mt^3 / Y3P** es la reingeniería integral y profesional del sistema administrativo institucional (*CRM-FCE*). 

Su propósito central es automatizar y asegurar el ciclo de vida digital de la comunidad académica (estudiantes y docentes) mediante la articulación fluida de cuatro ecosistemas clave:

1. **SIAL / UTI:** Ingesta de padrones de ingresantes, aspirantes y alumnos regulares.
2. **Microsoft 365 (Microsoft Graph API):** Creación de identidades institucionales (`@eco.unrc.edu.ar`), asignación de licencias A1 y control de acceso.
3. **Moodle FCE (Web Services REST):** Creación de usuarios, matriculación semestral automática por cátedra/comisión y gestión de grupos.
4. **Notificaciones Institucionales (SMTP):** Envío transaccional y seguro de credenciales de acceso inicial.

---

## 🎯 ¿Por qué se rediseñó el sistema?

El prototipo anterior presentaba limitaciones críticas de arquitectura que comprometían la estabilidad en producción:
* **Monolito inmanejable:** Más de 5.100 líneas concentradas en un único archivo.
* **Bloqueos por concurrencia:** Uso de SQLite en entornos multioperador, ocasionando errores de bloqueo de base de datos (`database is locked`).
* **Riesgo en segundo plano:** Tareas pesadas procesadas con hilos locales de Windows (`ThreadPoolExecutor`) que se interrumpían ante caídas de servicio.
* **Fallas de simulación:** Pruebas que podían modificar cuentas o grupos reales en los servidores de la facultad.

**Mt^3 / Y3P resuelve esto mediante una arquitectura desacoplada, orientada al dominio y lista para producción.**

---

## 🏗️ Arquitectura y Stack Tecnológico

El proyecto está construido bajo los más altos estándares del desarrollo web moderno con Python:

* **Backend Framework:** Django 6.1 (LTS) con arquitectura modular por Apps (*Bounded Contexts*).
* **Gestor de Entorno y Paquetes:** `uv` (Astral) — Determinismo absoluto mediante `uv.lock`.
* **Base de Datos:** PostgreSQL 16 sobre contenedor Docker con transacciones ACID y soporte para concurrencia real.
* **Driver DB:** `psycopg 3` (conexión nativa de alto rendimiento).
* **Configuración:** `django-environ` con soporte estricto de variables twelve-factor (`.env`).
* **Localización Regional:** Zona horaria `America/Argentina/Cordoba` e idioma `es-ar`.

---

## 📂 Estructura Modular del Proyecto

Siguiendo las mejores prácticas de Django, los módulos de negocio se encuentran organizados dentro del paquete contenedor `apps/`:

```text
y3p/
├── config/                  # Núcleo de configuración global (settings, urls, wsgi)
├── apps/                    # Módulos de negocio desacoplados
│   ├── platform_mocks/      # Simulador local de APIs (UTI, Graph, Moodle) para dev
│   ├── people/              # Modelos de estudiantes, docentes y cursadas
│   ├── provisioning/        # Motor orquestador de altas, bajas y licencias
│   └── auditing/            # Snapshots, cruces de actividad D180 y bajas protegidas
├── docs/                    # Bitácoras de arquitectura y guías de desarrollo
├── manage.py                # Gestor de comandos de Django
└── pyproject.toml           # Declaración unificada de dependencias (uv)