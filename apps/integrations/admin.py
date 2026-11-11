"""Administración de la tabla de convertibilidad de ingreso."""

from django.contrib import admin

from apps.people.admin import SuperuserConfigurationAdmin
from .models import AdmissionCourseRule


@admin.register(AdmissionCourseRule)
class AdmissionCourseRuleAdmin(SuperuserConfigurationAdmin):
    list_display = (
        'order', 'career_code', 'modality', 'commission_pattern',
        'target_courses', 'target_group', 'is_active',
    )
    list_display_links = ('career_code',)
    list_editable = ('order', 'is_active')
    list_filter = ('is_active', 'career_code', 'modality')
    search_fields = ('career_code', 'commission_pattern', 'target_courses', 'target_group')
