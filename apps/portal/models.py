"""Preferencias de segmentación nominal, sin almacenar SQL ni código ejecutable."""

from django.conf import settings
from django.db import models


class AdminQuerySnippet(models.Model):
    """Consulta privada o compartida que se recompila mediante el parser seguro."""

    name = models.CharField('Nombre', max_length=150)
    query_expression = models.TextField('Fórmula', blank=True, max_length=2000)
    filter_params = models.JSONField('Filtros complementarios', default=dict, blank=True)
    is_shared = models.BooleanField('Compartido con administración', default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name='Creado por',
                                   on_delete=models.CASCADE, related_name='admin_query_snippets')
    created_at = models.DateTimeField('Creado el', auto_now_add=True)
    updated_at = models.DateTimeField('Actualizado el', auto_now=True)

    class Meta:
        verbose_name = 'Vista guardada de administración'
        verbose_name_plural = 'Vistas guardadas de administración'
        ordering = ('name', 'pk')

    def __str__(self):
        """Identifica la preferencia por su nombre institucional."""
        return self.name
