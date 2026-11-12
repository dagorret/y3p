"""Exportación tabular de la consulta filtrada con optimización anti-N+1.

La misma consulta SQL gobierna pantalla y archivo. La generación por bloques
precarga vinculaciones sin consultar por alumno; el libro de solo escritura
limita la memoria. Cada identidad ocupa una fila y sus vinculaciones conservan
el mismo orden dentro de las columnas académicas.
"""

from tempfile import SpooledTemporaryFile

from django.http import FileResponse, HttpResponseBadRequest
from django.urls import reverse
from django.utils import timezone
from django.views import View
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from .query_parser import QuerySyntaxError
from .snippet_services import resolve_query_params, visible_snippets
from .permissions import StaffPortalMixin
from .student_grid import HEALTH_LABELS, student_queryset


class StudentExportView(StaffPortalMixin, View):
    """Exige lectura antes de generar el archivo con todos los resultados."""

    def get(self, request):
        try:
            params, _ = resolve_query_params(request.GET, visible_snippets(request.user))
            queryset = student_queryset(params)
        except QuerySyntaxError as exc:
            #FEXPORT03-8304: La fórmula inválida se rechaza sin exportar una consulta más amplia.
            return HttpResponseBadRequest('; '.join(exc.messages), content_type='text/plain; charset=utf-8')
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet('Alumnos')
        sheet.append(['Selección', 'DNI', 'Apellido y Nombre', 'UPN Institucional',
                      'Carrera', 'Estado Saga', 'Vencimiento TTL', 'Salud / Semáforo', 'Acciones'])
        for identity in queryset.iterator(chunk_size=200):
            affiliations = identity.grade_affiliations
            values = ['', identity.identifier, f'{identity.last_name}, {identity.first_name}',
                      identity.institutional_upn or 'UPN pendiente',
                      '\n'.join(a.reference or 'Sin carrera' for a in affiliations),
                      '\n'.join(a.current_status.code for a in affiliations),
                      '\n'.join(timezone.localtime(a.expires_at).strftime('%d/%m/%Y %H:%M:%S')
                                if a.expires_at else 'Indefinida' for a in affiliations),
                      HEALTH_LABELS[identity.health],
                      request.build_absolute_uri(reverse('portal:identity360', args=[identity.pk]))]
            cells = []
            for value in values:
                cell = WriteOnlyCell(sheet, value=value)
                #FEXPORT01-8001: Todo dato institucional se escribe como texto, sin ejecutar fórmulas.
                cell.data_type = 's'
                cells.append(cell)
            sheet.append(cells)
        output = SpooledTemporaryFile(max_size=4 * 1024 * 1024)
        workbook.save(output)
        output.seek(0)
        #FEXPORT02-8002: Respuesta descargable autorizada con tipo Excel explícito.
        return FileResponse(output, as_attachment=True, filename='alumnos.xlsx',
                            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
