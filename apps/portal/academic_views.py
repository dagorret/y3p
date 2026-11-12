"""Consolas académicas aisladas por ámbito con observación local no bloqueante.

Las siete consultas de la grilla se reutilizan con un scope fijado en la ruta;
la ficha limitada solo precarga vinculaciones del mismo ámbito. Confirmar PRE
usa el servicio atómico del dominio y las APIs se ejecutan mediante el Outbox.
"""

from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.views import View

from apps.people.scopes import scope_url
from .permissions import StaffPortalMixin
from .student_grid import student_queryset, describe_health


class ScopedIdentityView(StaffPortalMixin, View):
    def get(self, request, identity_id):
        identity = student_queryset({}, scope=self.scope).filter(pk=identity_id).first()
        if identity is None:
            #FACADEMICVIEW01-10201: Una ficha ajena recibe 403 sin exponer sus datos ni vinculaciones.
            raise PermissionDenied('La identidad no pertenece a este ámbito académico.')
        describe_health([identity])
        #FACADEMICVIEW02-10202: Solo se muestran vinculaciones precargadas del ámbito autorizado.
        return render(request, 'portal/administracion/scoped_person.html', {
            'identity': identity, 'scope': self.scope, 'grid_url': scope_url(self.scope)})
