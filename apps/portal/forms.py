"""Input validation for public registration dashboards."""

import re

from django import forms
from django.core.exceptions import ValidationError


class RegistrationForm(forms.Form):
    """Shared person fields for postgraduate and extension registrations."""

    dni = forms.CharField(label="DNI", max_length=20)
    first_name = forms.CharField(label="Nombres", max_length=150)
    last_name = forms.CharField(label="Apellidos", max_length=150)
    personal_email = forms.EmailField(label="Correo personal", max_length=150)

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
        """Normalize common DNI separators and require a valid Argentine length."""

        dni = re.sub(r"[.\s]", "", self.cleaned_data["dni"])
        if not re.fullmatch(r"\d{7,8}", dni):
            raise ValidationError("Ingresá un DNI válido de 7 u 8 dígitos.")
        return dni

    def clean_first_name(self):
        return self.cleaned_data["first_name"].strip()

    def clean_last_name(self):
        return self.cleaned_data["last_name"].strip()


class PostgradoRegistrationForm(RegistrationForm):
    """Registration fields for postgraduate students."""

    program = forms.CharField(label="Carrera o programa", max_length=150)

    def clean_program(self):
        return self.cleaned_data["program"].strip()


class ExtensionRegistrationForm(RegistrationForm):
    """Registration fields for extension courses and diplomas."""

    course = forms.CharField(label="Curso de extensión", max_length=150)

    def clean_course(self):
        return self.cleaned_data["course"].strip()
