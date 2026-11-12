"""Comandos de preferencias y respuesta tabular reactiva con autorización.

El parser traduce listas blancas a Q parametrizados y EXISTS correlacionados;
la vista reutiliza las siete consultas de grilla, incluyendo la lista visible de
snippets. Los filtros se sincronizan mediante un reemplazo HTMX fuera de banda.
"""

from django.core.exceptions import ValidationError
from django.shortcuts import render
from django.views import View

from apps.people.permissions import OPERATE_STUDENT_DAILY
from .permissions import StaffPortalMixin
from .snippet_services import FILTER_KEYS, create_snippet, delete_snippet
from .student_grid import student_grid_context


class SnippetCreateView(StaffPortalMixin, View):
    required_permission = OPERATE_STUDENT_DAILY

    def post(self, request):
        error = False
        try:
            snippet = create_snippet(user=request.user, name=request.POST.get('snippet_name', ''),
                expression=request.POST.get('formula', ''),
                filter_params={key: request.POST[key] for key in FILTER_KEYS if request.POST.get(key)},
                is_shared=request.POST.get('is_shared') == 'on', scope=self.scope)
            feedback = f'Vista guardada: {snippet.name}.'
        except ValidationError as exc:
            #FSNIPPETVIEW01-8401: Presenta errores de sintaxis y esquema como retroalimentación legible.
            error, feedback = True, '; '.join(exc.messages)
        context = student_grid_context(request.POST, user=request.user, feedback=feedback, error=error, builder_mode=request.POST.get('builder_mode') == '1', scope=self.scope)
        context['refresh_filters'] = True
        #FSNIPPETVIEW02-8402: El fragmento confirma creación o validación sin requerir recarga completa.
        return render(request, 'portal/fragments/query_builder.html' if context['builder_mode'] else 'portal/fragments/administracion_results.html', context)


class SnippetDeleteView(StaffPortalMixin, View):
    required_permission = OPERATE_STUDENT_DAILY

    def post(self, request, snippet_id):
        delete_snippet(user=request.user, snippet_id=snippet_id, scope=self.scope)
        context = student_grid_context(request.POST, user=request.user, feedback='Vista guardada eliminada.', builder_mode=request.POST.get('builder_mode') == '1', scope=self.scope)
        context['refresh_filters'] = True
        #FSNIPPETVIEW03-8403: Solo confirma la eliminación después de comprobar autoría o administración.
        return render(request, 'portal/fragments/query_builder.html' if context['builder_mode'] else 'portal/fragments/administracion_results.html', context)
