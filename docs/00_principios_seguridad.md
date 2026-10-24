🛡️ Principios Clave de Diseño y Seguridad

    Simulación Local Real (platform_mocks): En modo de desarrollo, el sistema emula las respuestas de Moodle, SIAL y Graph localmente en PostgreSQL. No se toca internet ni se alteran servidores de producción durante las pruebas.

    Idempotencia y Éxitos Parciales: Las operaciones remotas se registran paso a paso. Si Moodle falla tras crear una cuenta en Microsoft, el sistema no duplica la cuenta ni envía contraseñas erróneas en el reintento.

    Auditoría y Bajas Protegidas: Cruce estricto de telemetría de inactividad (D180) y validación en tiempo real antes de efectuar cualquier baja de cuenta.

    Transaccionalidad Fuerte: Esquemas relacionales con integridad referencial formal (Foreign Keys estrictas).

🚀 Puesta en Marcha Local (Desarrollo)
1. Requisitos Previos

    Python 3.12+

    Docker y Docker Compose (para PostgreSQL 16)

    Gestor de paquetes uv instalado (curl -LsSf https://astral.sh/uv/install.sh | sh)

2. Clonar e Instalar Dependencias
Bash

git clone [https://github.com/dagorret/repo_tempo_y3.git](https://github.com/dagorret/repo_tempo_y3.git) y3p
cd y3p

# Sincronizar el entorno virtual hermético
uv sync

3. Configurar Variables de Entorno

Copia la plantilla base y completa los valores locales:
Bash

cp .env.example .env

4. Verificar Conexión a Base de Datos
Bash

uv run python manage.py shell -c "from django.db import connection; cursor = connection.cursor(); cursor.execute('SELECT version();'); print('PostgreSQL Conectado:'); print(cursor.fetchone()[0])"

5. Iniciar el Servidor de Desarrollo
Bash

uv run python manage.py runserver

Acceder en el navegador a: http://127.0.0.1:8000
📖 Documentación Técnica Adicional

Para más detalles sobre las decisiones de diseño y el registro de arquitectura, consultar la carpeta docs/:

    docs/01_inicializacion_entorno.md: Bitácora técnica del stack y configuración del entorno.

Facultad de Ciencias Económicas — UNRC

Desarrollo institucional mantenible, transparente y centrado en la continuidad operativa.