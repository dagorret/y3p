"""Configuración WSGI del proyecto.

Expone la aplicación como variable de módulo application. Referencia:
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()
