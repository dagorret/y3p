"""Validación de formularios de inscripción y administración operativa."""

import re

from django import forms
from django.core.exceptions import ValidationError


class RegistrationForm(forms.Form):
    """Campos personales comunes para las inscripciones de posgrado y extensión."""

    dni = forms.CharField(label="DNI", max_length=20)
    first_name = forms.CharField(label="Nombres", max_length=150)
    last_name = forms.CharField(label="Apellidos", max_length=150)
    personal_email = forms.EmailField(label="Correo personal", max_length=150)
    initial_status_code = forms.ChoiceField(
        label="Estado inicial",
        choices=(("REGULAR", "Regular"), ("PRE", "Preinscripto")),
        initial="REGULAR",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update(
                {
                    "class": (
                        "w-full rounded-lg border border-slate-300 px-3 py-2 "
                        "outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-100"
                    )
                }
            )
        self.fields["dni"].widget.attrs.update(
            {"inputmode": "numeric", "autocomplete": "off"}
        )

    def clean_dni(self):
        """Normaliza separadores usuales y exige un DNI argentino de longitud válida."""

        dni = re.sub(r"[.\s]", "", self.cleaned_data["dni"])
        if not re.fullmatch(r"\d{7,8}", dni):
            #FPORTALFORMS001-8001: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
            raise ValidationError("Ingresá un DNI válido de 7 u 8 dígitos.")
        return dni

    def clean_first_name(self):
        return self.cleaned_data["first_name"].strip()

    def clean_last_name(self):
        return self.cleaned_data["last_name"].strip()


class PostgradoRegistrationForm(RegistrationForm):
    """Campos de inscripción de estudiantes de posgrado."""

    program = forms.CharField(label="Carrera o programa", max_length=150)

    def clean_program(self):
        return self.cleaned_data["program"].strip()


class ExtensionRegistrationForm(RegistrationForm):
    """Campos de inscripción de cursos y diplomaturas de extensión."""

    course = forms.CharField(label="Curso de extensión", max_length=150)

    def clean_course(self):
        return self.cleaned_data["course"].strip()


class IdentityContactForm(forms.Form):
    first_name = forms.CharField(label='Nombres', max_length=150)
    last_name = forms.CharField(label='Apellidos', max_length=150, required=False)
    personal_email = forms.EmailField(label='Correo personal', max_length=150, required=False)
    phone = forms.CharField(label='Teléfono', max_length=50, required=False)
    city = forms.CharField(label='Ciudad', max_length=100, required=False)
    address = forms.CharField(label='Domicilio', max_length=255, required=False)


class MetadataForm(forms.Form):
    operation = forms.ChoiceField(choices=(('add', 'Añadir o actualizar'), ('remove', 'Quitar')))
    key = forms.CharField(label='Clave', max_length=150)
    value = forms.CharField(label='Valor (texto o JSON)', required=False, max_length=5000)


class TransitionForm(forms.Form):
    rule = forms.ModelChoiceField(queryset=None, label='Transición válida')
    expected_from_code = forms.CharField(widget=forms.HiddenInput)
    reason = forms.CharField(label='Motivo', max_length=255)

    def __init__(self, *args, affiliation, **kwargs):
        from apps.people.models import SagaTransitionRule
        super().__init__(*args, **kwargs)
        self.fields['rule'].queryset = SagaTransitionRule.objects.filter(
            profile_type=affiliation.profile_type, from_status=affiliation.current_status,
        ).select_related('from_status', 'to_status', 'profile_type')
        self.fields['expected_from_code'].initial = affiliation.current_status.code


class TTLForm(forms.Form):
    expires_at = forms.DateField(label='Vencimiento', widget=forms.DateInput(attrs={'type': 'date'}))
    reason = forms.CharField(label='Motivo del cambio de vigencia', max_length=255)


class MoodleExpressForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.integrations.models import MoodleCourseMirror
        self.fields['shortname'].widget = forms.Select(choices=[('', 'Seleccione un curso')] +
            [(course.shortname, f'{course.shortname} · {course.fullname}') for course in MoodleCourseMirror.objects.all()])

    shortname = forms.CharField(label='Shortname del curso', max_length=100)
    role_id = forms.TypedChoiceField(label='Rol', coerce=int, choices=((5, 'Estudiante'), (3, 'Docente')))
    group_name = forms.CharField(label='Comisión (opcional)', max_length=100, required=False)


class ManualPersonForm(forms.Form):
    dni = forms.CharField(label='DNI', max_length=20)
    first_name = forms.CharField(label='Nombres', max_length=150)
    last_name = forms.CharField(label='Apellidos', max_length=150)
    personal_email = forms.EmailField(label='Correo personal', max_length=150)
    reference = forms.CharField(label='Carrera / referencia', max_length=150,
        help_text='Para ALUMNO use el código de carrera de la tabla de convertibilidad; para STAFF, el área o función.')
    role = forms.ChoiceField(label='Rol institucional', choices=(('ALUMNO', 'Alumno'), ('STAFF', 'Staff')))

    def clean_dni(self):
        dni = re.sub(r'[.\s]', '', self.cleaned_data['dni'])
        if re.fullmatch(r'[0-9]{7,8}', dni) is None:
            #FPORTALFORMS002-8002: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
            raise ValidationError('Ingresá un DNI de 7 u 8 dígitos.')
        return dni


class FunctionalAccountForm(forms.Form):
    identifier = forms.CharField(label='Código organizacional (no DNI)', max_length=50)
    first_name = forms.CharField(label='Denominación de la unidad / cuenta', max_length=150)
    institutional_upn = forms.EmailField(label='UPN oficial', max_length=150)
    personal_email = forms.EmailField(label='Correo de contacto del responsable', max_length=150,
        help_text='Las credenciales se enviarán a este contacto cuando se aprovisione la cuenta.')
    reference = forms.CharField(label='Área / cátedra / referencia', max_length=150)

    def clean_identifier(self):
        code = self.cleaned_data['identifier'].strip().upper()
        if not re.fullmatch(r'[A-Z][A-Z0-9_-]*', code):
            #FPORTALFORMS003-8003: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
            raise ValidationError('Use un código que comience con una letra y contenga letras, números, guiones o guiones bajos.')
        return code

    def clean_institutional_upn(self):
        upn = self.cleaned_data['institutional_upn'].lower()
        if not upn.endswith('@eco.unrc.edu.ar'):
            #FPORTALFORMS004-8004: Origina ValidationError; el llamador conserva la causa y decide su tratamiento.
            raise ValidationError('El UPN debe pertenecer a @eco.unrc.edu.ar.')
        return upn


class TeamCreationForm(forms.Form):
    display_name = forms.CharField(label='Nombre del equipo / cátedra', max_length=255)
    description = forms.CharField(label='Descripción', max_length=2000, required=False, widget=forms.Textarea(attrs={'rows': 3}))
    owner_upn = forms.EmailField(label='UPN del docente propietario', max_length=150)
    moodle_shortname = forms.CharField(label='Curso Moodle asociado (shortname)', max_length=100, required=False)
    career_code = forms.CharField(label='Carrera SIAL asociada', max_length=20, required=False)
    commission = forms.CharField(label='Comisión asociada (opcional)', max_length=100, required=False)


class TeamMappingForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.integrations.models import MoodleCourseMirror
        self.fields['moodle_shortname'].widget = forms.Select(choices=[('', 'Sin curso asociado')] +
            [(course.shortname, f'{course.shortname} · {course.fullname}') for course in MoodleCourseMirror.objects.all()])

    moodle_shortname = forms.CharField(label='Curso Moodle (shortname)', max_length=100, required=False)
    career_code = forms.CharField(label='Carrera SIAL', max_length=20, required=False)
    commission = forms.CharField(label='Comisión (vacío: todos)', max_length=100, required=False)


class TeamPopulationForm(forms.Form):
    source = forms.ChoiceField(label='Origen de alumnos', choices=(('moodle', 'Moodle'), ('sial', 'SIAL / ingresantes')))
    campaign = forms.CharField(label='Campaña SIAL', max_length=100, required=False)

    def clean(self):
        data = super().clean()
        if data.get('source') == 'sial' and not data.get('campaign'):
            #FPORTALFORMS005-8005: Devuelve un error de validación sin confirmar una operación inválida.
            self.add_error('campaign', 'Indique la campaña SIAL.')
        return data


class TeamPurgeForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.integrations.models import TeamsWorkspaceMirror
        self.fields['team_id'].widget = forms.Select(choices=[('', 'Seleccione un equipo')] +
            [(team.team_id, team.display_name) for team in TeamsWorkspaceMirror.objects.all()])

    team_id = forms.UUIDField(label='Identificador del equipo (UUID)')
    confirm = forms.BooleanField(label='Confirmo la limpieza de miembros conservando propietarios', required=True)
