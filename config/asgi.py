"""Configuración ASGI del proyecto.

Expone la aplicación como variable de módulo application. La configuración
de despliegue se describe en https://docs.djangoproject.com/en/6.1/howto/deployment/asgi/.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_asgi_application()
