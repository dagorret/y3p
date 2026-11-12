"""Control de catálogos locales y agenda semanal sin llamadas remotas durante GET.

Los selectores usan claves foráneas y la pivot relacional del espejo; las lecturas
remotas solo se ejecutan en Celery o en un POST explícito de refresco individual.
"""

from django import forms
from django.shortcuts import render
from django.views import View
from django.db import transaction
from kombu.exceptions import OperationalError

from apps.integrations.catalogs import ensure_sync_states, next_scheduled_run
from apps.integrations.models import CatalogSyncState, TeamsWorkspaceMirror, TeamsChannelMirror
from apps.integrations.tasks import sync_catalogs_task
from apps.people.permissions import ADMINISTRATE_STUDENT_CRITICAL
from .permissions import StaffPortalMixin


class CatalogScheduleForm(forms.Form):
    source = forms.ChoiceField(label='Origen', choices=(('TEAMS', 'Teams'), ('MOODLE', 'Moodle')))
    schedule_weekday = forms.TypedChoiceField(label='Día de la semana', coerce=int,
        choices=tuple(enumerate(('Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'))))
    schedule_hour = forms.TimeField(label='Hora local', input_formats=['%H:%M'], widget=forms.TimeInput(format='%H:%M', attrs={'type': 'time'}))


class CatalogSyncView(StaffPortalMixin, View):
    required_permission = ADMINISTRATE_STUDENT_CRITICAL

    def show(self, request, *, feedback='', error=False, form=None):
        states = ensure_sync_states()
        for state in states:
            state.schedule_form = CatalogScheduleForm(initial={'source': state.source,
                'schedule_weekday': state.schedule_weekday, 'schedule_hour': state.schedule_hour},
                auto_id=f'{state.source}_%s')
        #FCATALOGVIEW01-9301: Respuesta de consulta local; nunca dispara sincronización remota.
        return render(request, 'portal/administracion/catalog_sync.html', {
            'states': states, 'feedback': feedback, 'error': error, 'invalid_form': form,
            'catalog_teams': TeamsWorkspaceMirror.objects.all(),
        })

    def get(self, request):
        return self.show(request)

    def post(self, request):
        if request.POST.get('action') == 'sync':
            try:
                sync_catalogs_task.delay()
            except OperationalError:
                #FCATALOGVIEW02-9302: Una cola no disponible no se presenta como trabajo confirmado.
                return self.show(request, feedback='No se pudo encolar la sincronización. Revise el servicio de tareas.', error=True)
            #FCATALOGVIEW03-9303: La respuesta confirma encolado, sin afirmar éxito remoto anticipado.
            return self.show(request, feedback='Sincronización de catálogos encolada.')
        form = CatalogScheduleForm(request.POST)
        if not form.is_valid():
            #FCATALOGVIEW04-9304: Los errores de agenda se muestran en castellano sin modificar la programación.
            return self.show(request, feedback='La programación no es válida.', error=True, form=form)
        ensure_sync_states()
        with transaction.atomic():
            state = CatalogSyncState.objects.select_for_update().get(source=form.cleaned_data['source'])
            state.schedule_weekday = form.cleaned_data['schedule_weekday']
            state.schedule_hour = form.cleaned_data['schedule_hour']
            state.next_run_at = next_scheduled_run(state)
            state.save(update_fields=['schedule_weekday', 'schedule_hour', 'next_run_at'])
        #FCATALOGVIEW05-9305: Confirma la programación ya persistida, que Beat leerá en su próximo ciclo.
        return self.show(request, feedback='Programación semanal actualizada.')


class CatalogChannelsView(StaffPortalMixin, View):
    def get(self, request):
        channels = TeamsChannelMirror.objects.filter(workspace__team_id=request.GET.get('team_id', ''))
        #FCATALOGVIEW06-9306: El selector de canales se obtiene del espejo local y conserva su equipo.
        return render(request, 'portal/fragments/catalog_channels.html', {'catalog_channels': channels})
