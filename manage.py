#!/usr/bin/env python
"""Utilidad de línea de comandos para las tareas administrativas de Django."""
import os
import sys


def main():
    """Ejecuta tareas administrativas con la configuración del proyecto."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
    #FMANAGE001-8001: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
    except ImportError as exc:
        #FMANAGE002-8002: Origina ImportError; el llamador conserva la causa y decide su tratamiento.
        raise ImportError(
            "No se pudo importar Django. Verifique que esté instalado y disponible "
            "en PYTHONPATH y que el entorno virtual del proyecto esté activado."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
