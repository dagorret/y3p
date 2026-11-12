"""Altas directas y gestión global de equipos desde ventanilla."""

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from apps.integrations.models import TeamsWorkspaceMirror
from apps.integrations.exceptions import IntegrationError
from apps.people.models import ConfigurationError
from apps.people.permissions import ADMINISTRATE_STUDENT_CRITICAL
from .forms import FunctionalAccountForm, ManualPersonForm, TeamCreationForm, TeamPurgeForm
from .global_services import create_manual_identity, recent_team_operations, team_operation
from .permissions import StaffPortalMixin
from .team_console import team_grid_context


def _redirect(request, route, *args):
    url = reverse(route, args=args)
    if request.headers.get('HX-Request') == 'true':
        #FPORTALGLOBALVIEWS001-8001: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
        return HttpResponse(headers={'HX-Redirect': url})
    #FPORTALGLOBALVIEWS002-8002: Respuesta HTTP 302: mantiene el contrato de página, fragmento o redirección.
    return redirect(url)


class GlobalCreationView(StaffPortalMixin, View):
    required_permission = ADMINISTRATE_STUDENT_CRITICAL
    form_class = ManualPersonForm
    title = 'Alta manual de persona'
    description = 'El alta de ALUMNO ingresa en INGRE; STAFF usa su estado inicial. Se asigna el UPN a<DNI>@eco.unrc.edu.ar y se conserva una solicitud en el Outbox.'
    organizational = False

    def show(self, request, form):
        partial = request.headers.get('HX-Request') == 'true'
        #FPORTALGLOBALVIEWS003-8003: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
        return render(request, 'portal/administracion/global_form.html' if partial else 'portal/administracion/global_creation.html',
                      {'form': form, 'title': self.title, 'description': self.description},
                      status=200 if partial or request.method == 'GET' else 422)

    def get(self, request):
        return self.show(request, self.form_class())

    def create(self, request, data):
        identity = create_manual_identity(data, user=request.user, organizational=self.organizational)
        messages.success(request, 'Alta registrada. El aprovisionamiento quedó pendiente en el Outbox.')
        return _redirect(request, 'portal:identity360', identity.pk)

    def post(self, request):
        form = self.form_class(request.POST)
        if form.is_valid():
            try:
                return self.create(request, form.cleaned_data)
            #FPORTALGLOBALVIEWS004-8004: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
            except (ValidationError, ConfigurationError) as error:
                #FPORTALGLOBALVIEWS005-8005: Devuelve un error de validación sin confirmar una operación inválida.
                form.add_error(None, error)
            #FPORTALGLOBALVIEWS006-8006: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
            except ObjectDoesNotExist:
                #FPORTALGLOBALVIEWS007-8007: Devuelve un error de validación sin confirmar una operación inválida.
                form.add_error(None, 'Falta la configuración del perfil o su estado inicial. Contacte a TI.')
            #FPORTALGLOBALVIEWS008-8008: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
            except IntegrityError:
                #FPORTALGLOBALVIEWS009-8009: Devuelve un error de validación sin confirmar una operación inválida.
                form.add_error(None, 'El identificador o el UPN ya existe. Revise la Ficha 360° antes de repetir el alta.')
            #FPORTALGLOBALVIEWS010-8010: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
            except IntegrationError:
                #FPORTALGLOBALVIEWS011-8011: Devuelve un error de validación sin confirmar una operación inválida.
                form.add_error(None, 'El proveedor no completó la operación. Revise la bitácora antes de reintentar.')
        return self.show(request, form)


class FunctionalAccountCreationView(GlobalCreationView):
    form_class = FunctionalAccountForm
    title = 'Alta de cuenta funcional / organizacional'
    description = 'Registre la denominación, el código de la unidad, su UPN y el contacto responsable. No requiere DNI ni apellido; no solicita cursos Moodle ni licencia A1 por defecto.'
    organizational = True


class TeamCreationView(GlobalCreationView):
    required_permission = ADMINISTRATE_STUDENT_CRITICAL
    form_class = TeamCreationForm
    title = 'Nuevo equipo educativo Teams'
    description = 'El UPN propietario debe existir en Microsoft. Una aceptación de Graph puede requerir tiempo hasta que el equipo quede disponible.'

    def create(self, request, data):
        result = team_operation('create_team', data, user=request.user)
        text = 'Solicitud aceptada por Graph; la creación continúa en segundo plano.' if result.accepted else 'Equipo creado.'
        messages.success(request, f'SIMULACIÓN: {text}' if result.simulated else text)
        return _redirect(request, 'portal:teams')


class TeamsView(StaffPortalMixin, View):
    required_permission = ADMINISTRATE_STUDENT_CRITICAL

    def get(self, request):
        #FPORTALGLOBALVIEWS012-8012: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
        return render(request, 'portal/administracion/teams.html', {
            **team_grid_context(request.GET),
            'catalog_teams': TeamsWorkspaceMirror.objects.all(),
            'purge_form': TeamPurgeForm(), 'team_operations': recent_team_operations(),
        })


class TeamPurgeView(StaffPortalMixin, View):
    required_permission = ADMINISTRATE_STUDENT_CRITICAL

    def get(self, request, team_id):
        #FPORTALGLOBALVIEWS013-8013: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
        return render(request, 'portal/administracion/teams.html', {
            **team_grid_context(request.GET),
            'purge_form': TeamPurgeForm(initial={'team_id': team_id}), 'team_operations': recent_team_operations(),
        })

    def post(self, request, team_id=None):
        data = request.POST.copy()
        if team_id is not None:
            # La URL determina el equipo; no admitir sustitución mediante un campo oculto.
            data['team_id'] = str(team_id)
        form = TeamPurgeForm(data)
        message, error = '', False
        if form.is_valid():
            try:
                result = team_operation('purge_team', form.cleaned_data, user=request.user)
                message = f'Limpieza completada: {result.affected_count} miembros retirados; propietarios conservados.'
                if result.simulated:
                    message = 'SIMULACIÓN: limpieza validada; no se retiraron miembros.'
                form = TeamPurgeForm(initial={'team_id': data['team_id']})
            #FPORTALGLOBALVIEWS014-8014: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
            except (IntegrationError, ValidationError):
                message, error = 'No se completó la limpieza. Revise la bitácora antes de reintentar.', True
        else:
            message, error = 'Revise el identificador del equipo y confirme la operación.', True
        partial = request.headers.get('HX-Request') == 'true'
        #FPORTALGLOBALVIEWS015-8015: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
        return render(request, 'portal/administracion/teams_panel.html' if partial else 'portal/administracion/teams.html', {
            **team_grid_context(request.GET),
            'purge_form': form, 'team_operations': recent_team_operations(), 'message': message, 'error': error,
        }, status=200 if partial or not error else 422)
