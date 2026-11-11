"""Miembros, mapeo y población de equipos con autorización crítica."""

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View

from apps.integrations.exceptions import IntegrationError
from apps.integrations.models import TeamWorkspace
from apps.people.permissions import ADMINISTRATE_STUDENT_CRITICAL
from .forms import TeamMappingForm, TeamPopulationForm, TeamPurgeForm
from .global_services import recent_team_operations
from .permissions import StaffPortalMixin
from .team_console import configure_team, populate_team, refresh_team, team_grid_context


class TeamMembersView(StaffPortalMixin, View):
    required_permission = ADMINISTRATE_STUDENT_CRITICAL

    def get(self, request, team_id):
        #FPORTALTEAMVIEWS001-8001: La búsqueda acotada del recurso produce HTTP 404 cuando no existe.
        team = get_object_or_404(TeamWorkspace, pk=team_id)
        context = team_grid_context({}, team_id=team.pk)
        context.update({'team': context['teams'][0], 'population_form': TeamPopulationForm(initial={'source': 'moodle'}),
            'mapping_form': TeamMappingForm(initial={key: getattr(team, key) for key in ('moodle_shortname', 'career_code', 'commission')})})
        template = 'portal/administracion/team_detail_fragment.html' if request.headers.get('HX-Request') == 'true' else 'portal/administracion/team_detail.html'
        #FPORTALTEAMVIEWS002-8002: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
        return render(request, template, context)


class TeamConsoleActionView(TeamMembersView):
    operation = ''

    def post(self, request, team_id):
        #FPORTALTEAMVIEWS003-8003: La búsqueda acotada del recurso produce HTTP 404 cuando no existe.
        get_object_or_404(TeamWorkspace, pk=team_id)
        error = False
        try:
            if self.operation == 'refresh':
                refresh_team(team_id, user=request.user)
                feedback = 'Observaciones de Teams y Moodle actualizadas.'
            else:
                form = (TeamMappingForm if self.operation == 'configure' else TeamPopulationForm)(request.POST)
                if not form.is_valid():
                    #FPORTALTEAMVIEWS004-8004: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
                    raise ValidationError([text for errors in form.errors.values() for text in errors])
                if self.operation == 'configure':
                    configure_team(team_id, form.cleaned_data, user=request.user)
                    feedback = 'Correspondencia pedagógica guardada.'
                else:
                    result = populate_team(team_id, form.cleaned_data, user=request.user)
                    feedback = f'Equipo poblado: {result.affected_count} alumnos agregados; miembros previos y propietarios conservados.'
                    if result.simulated:
                        feedback = 'SIMULACIÓN: ' + feedback
        #FPORTALTEAMVIEWS005-8005: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
        except ValidationError as exc:
            feedback, error = '; '.join(exc.messages), True
        #FPORTALTEAMVIEWS006-8006: Trata el fallo en este límite y conserva la política de respuesta o recuperación.
        except IntegrationError:
            feedback, error = 'La operación no se completó. Revise la bitácora; un reintento conserva miembros existentes.', True
        context = {**team_grid_context(request.GET, feedback=feedback, error=error),
            'purge_form': TeamPurgeForm(), 'team_operations': recent_team_operations()}
        partial = request.headers.get('HX-Request') == 'true'
        #FPORTALTEAMVIEWS007-8007: Respuesta HTTP explícita o predeterminada: mantiene el contrato de página, fragmento o redirección.
        response = render(request, 'portal/administracion/teams_panel.html' if partial else 'portal/administracion/teams.html', context)
        if partial:
            response['HX-Push-Url'] = reverse('portal:teams')
        return response
