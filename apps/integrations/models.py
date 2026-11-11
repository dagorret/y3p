"""Configuración persistida de conversión de ingreso a cursos pedagógicos."""

import re

from django.core.exceptions import ValidationError
from django.db import models


class AdmissionCourseRule(models.Model):
    order = models.IntegerField(default=10, help_text="Prioridad de evaluación. Primer match gana.")
    career_code = models.CharField(
        max_length=20, blank=True, null=True,
        help_text="Código carrera (ej: 2, 3, 4, 7, 8) o NULL para comodín.",
    )
    modality = models.CharField(
        max_length=10, blank=True, null=True,
        help_text="'1', '2', 'P', 'D' o NULL para comodín.",
    )
    commission_pattern = models.CharField(
        max_length=50, blank=True, null=True,
        help_text="Regex o texto de comisión elegida (ej: 'COMISION 1', 'COMISION 2') o NULL.",
    )
    target_courses = models.CharField(
        max_length=255,
        help_text="Shortnames de Moodle separados por coma (ej: 'I3,I4,I5,I6,I1').",
    )
    target_group = models.CharField(
        max_length=100, blank=True, null=True,
        help_text="Nombre de grupo/comisión en Moodle a asignar.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'id']
        verbose_name = "Regla de cursos de ingreso"
        verbose_name_plural = "Reglas de cursos de ingreso"

    def clean(self):
        super().clean()
        if self.commission_pattern and self.commission_pattern.strip():
            try:
                re.compile(self.commission_pattern.strip(), re.IGNORECASE)
            except re.error as error:
                raise ValidationError({'commission_pattern': 'La expresión regular no es válida.'}) from error
        if not any(item.strip() for item in self.target_courses.split(',')):
            raise ValidationError({'target_courses': 'Debe indicar al menos un shortname.'})

    @classmethod
    def resolve_courses(cls, career: str, modality: str, commission: str) -> tuple[list[str], str | None]:
        """Primer match activo; regex de comisión completa, sin distinguir mayúsculas.

        Career y modality se comparan como texto sin espacios exteriores.
        NULL/vacío en la regla es comodín. Sin coincidencias retorna ([], None).
        """

        from apps.integrations.exceptions import ConfigurationError

        career, modality, commission = (str(value or '').strip() for value in (career, modality, commission))
        for rule in cls.objects.filter(is_active=True).order_by('order', 'id'):
            if rule.career_code and rule.career_code.strip() and rule.career_code.strip() != career:
                continue
            if rule.modality and rule.modality.strip() and rule.modality.strip() != modality:
                continue
            pattern = (rule.commission_pattern or '').strip()
            try:
                if pattern and re.fullmatch(pattern, commission, re.IGNORECASE) is None:
                    continue
            except re.error as error:
                raise ConfigurationError('Expresión de comisión inválida en regla de ingreso.') from error
            courses = list(dict.fromkeys(item.strip() for item in rule.target_courses.split(',') if item.strip()))
            if not courses:
                raise ConfigurationError('La regla de ingreso no contiene cursos Moodle.')
            return courses, (rule.target_group or '').strip() or None
        return [], None

    def __str__(self):
        return f"{self.order}: {self.career_code or '*'} → {self.target_courses}"
