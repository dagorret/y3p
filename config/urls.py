"""Rutas del proyecto Django.

La lista urlpatterns conecta el administrador técnico y los portales
operativos con sus vistas. Referencia:
https://docs.djangoproject.com/en/6.1/topics/http/urls/.
"""
from django.contrib import admin
from django.urls import include, path

#FHTTP403-9001: Django convierte PermissionDenied en 403; el origen está etiquetado en la validación RBAC.
#FHTTP404-9002: Django convierte Http404 en 404; cada búsqueda de recurso identifica su regla local.
#FHTTP500-9003: Una excepción no controlada conserva su causa y llega al 500 de Django; DEBUG regula su presentación.
# Estos comentarios documentan los manejadores predeterminados y no reemplazan sus garantías de seguridad.
urlpatterns = [
    path('admin/', admin.site.urls),
    path('dash/', include('apps.portal.urls')),
]
