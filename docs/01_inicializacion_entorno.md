# 01. Inicialización del Entorno, Stack y Arquitectura Base

**Proyecto:** Mt^3/Y3P (FCE - UNRC)  
**Fecha:** 24 de octubre de 2026  
**Autor:** Carlos Dagorret  
**Objetivo:** Rediseño modular, mantenible y profesional del sistema administrativo (reemplazo del CRM-FCE heredado en FastAPI).

---

## 1. Versiones del Stack Tecnológico

  | Componente | Versión Exacta / Fijada | Rol en el Sistema |
| :--- | :--- | :--- |
| **Python** | `3.12.15` (CPython Linux x86_64) | Runtime del lenguaje fijado herméticamente vía `.python-version`. |
| **Gestor de Paquetes** | `uv` (Astral) | Resolución de dependencias ultrarrápida, entorno virtual aislado (`.venv`) y bloqueo determinista (`uv.lock`). |
| **Framework Web** | `Django 6.1.2` | Núcleo de la aplicación, ORM, migraciones automáticas y seguridad web (CSRF, cookies firmadas). |
| **Driver PostgreSQL** | `psycopg 3.2+` (Binary) | Conector moderno nativo de Python para comunicación eficiente y pooling con PostgreSQL. |
| **Variables de Entorno**| `django-environ 0.11.2` | Parseo seguro de variables tipo twelve-factor app (`.env`) y URLs de conexión (`DATABASE_URL`). |
| **Motor de Base de Datos** | `PostgreSQL 16.15` (Debian en Docker) | Base de datos relacional con soporte para transacciones ACID reales, bloqueo de filas y concurrencia. |

---

## 2. Decisiones de Arquitectura y "Por Qué" Técnico

### A. Reemplazo de SQLite por PostgreSQL 16
* **Problema heredado:** El prototipo anterior utilizaba SQLite (`crm_fce.sqlite3`), lo que generaba bloqueos de base de datos (`database is locked`) y no permitía concurrencia real entre múltiples operadores o tareas en segundo plano.
* **Decisión:** PostgreSQL 16 permite transacciones atómicas concurrentes (`SELECT FOR UPDATE`), consistencia estricta de claves foráneas y un soporte robusto para la carga masiva de lotes de alumnos.

### B. Modularización con Django vs. Monolito de FastAPI
* **Problema heredado:** El sistema anterior concentró más de 5.100 líneas en un único archivo (`app/main.py`), mezclando pantallas HTML, llamadas a APIs externas y procesamiento de archivos en hilos locales frágiles.
* **Decisión:** Django impone una separación de responsabilidades clara mediante **Apps** desacopladas (Bounded Contexts):
  * Cada módulo maneja sus propias tablas (`models.py`), pantallas (`views.py`) y migraciones.

### C. La carpeta organizadora `apps/`
* **Por qué existe:** Por defecto, Django crea las aplicaciones sueltas en la raíz del proyecto. Para evitar amontonar carpetas al crecer el sistema, se utiliza `apps/` como un paquete contenedor (`__init__.py`) donde residirán los módulos del negocio (`people`, `provisioning`, `auditing`, etc.).

### D. La app `platform_mocks` (Simulador Local)
* **Problema heredado:** En el sistema viejo, el modo simulación (`DRY_RUN`) fallaba porque llamaba a APIs reales de Moodle o borraba identidades de Microsoft por error (Fallo H-03).
* **Decisión:** Se creó `platform_mocks` como el primer módulo del proyecto para actuar como un "doble de riesgo". Simula localmente las respuestas de UTI (SIAL), Moodle y Microsoft Graph sin conectarse a internet ni tocar servidores de producción durante el desarrollo.

---

## 3. Registro Cronológico del Proceso de Configuración

### Paso 1: Inicialización del entorno con `uv`
Se limpiaron herramientas heredadas del sistema operativo y se ancló el intérprete de Python:
```bash
uv init --python 3.12.15
uv add "django>=6.1,<6.2" "django-environ>=0.11.2" "psycopg[binary]>=3.2"
rm -f hello.py
