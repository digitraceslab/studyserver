import json
from django.contrib import admin
from django.db import models
from django import forms
from django.utils.html import format_html
from django.urls import reverse
from django_ace import AceWidget
from .models import Study, Consent, StudyParticipant, StudySourceConfiguration
from .forms import StudyAdminForm, SourceConfigurationInlineForm
from data_sources.models import DataSource


class PrettyJSONFormField(forms.JSONField):
    widget = AceWidget(mode='json', theme='monokai', width='100%', height='300px')

    def prepare_value(self, value):
        if isinstance(value, (dict, list)):
            return json.dumps(value, indent=2)
        return super().prepare_value(value)

@admin.register(StudyParticipant)
class StudyParticipantAdmin(admin.ModelAdmin):
    list_display = ('study', 'participant_display', 'pseudo_id')
    readonly_fields = ('pseudo_id',)

    @admin.display(description='Participant')
    def participant_display(self, obj):
        if obj.participant:
            return obj.participant.user.username
        return f"[deleted-{obj.pseudo_id}]"


@admin.register(Consent)
class ConsentAdmin(admin.ModelAdmin):
    list_display = (
        'study',
        'participant_username',
        'participant_pseudo_id',
        'source_type',
        'data_source_status',
        'is_complete',
        'consent_date'
    )
    researcher_readonly_fields = (
        'study',
        'participant',
        'source_type',
        'data_source',
        'consent_text_accepted',
        'is_complete',
        'consent_date',
        'revocation_date'
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        
        return qs.filter(study__researchers=request.user.profile)

    def get_readonly_fields(self, request, obj=None):
        if request.user.is_superuser:
            return ('participant', 'study', 'source_type', 'consent_date')
        return self.researcher_readonly_fields


    @admin.display(description='Participant')
    def participant_username(self, obj):
        return obj.participant.user.username

    @admin.display(description='Participant ID')
    def participant_pseudo_id(self, obj):
        if obj.study_participant:
            return obj.study_participant.pseudo_id
        return "-"

    @admin.display(description='Source Status')
    def data_source_status(self, obj):
        if not obj.data_source:
            return "Not linked"
        source = obj.data_source.get_real_instance()
        return f"{source.name} ({source.status})"


class SourceConfigurationInline(admin.TabularInline):
    model = StudySourceConfiguration
    form = SourceConfigurationInlineForm
    extra = 1
    fields = ['source_type', 'status', 'data_start', 'data_end', 'change_link']
    readonly_fields = ['change_link']

    @admin.display(description='')
    def change_link(self, obj):
        if not obj.pk:
            return '-'
        url = reverse('admin:studies_studysourceconfiguration_change', args=[obj.pk])
        return format_html('<a href="{}">Edit</a>', url)


@admin.register(StudySourceConfiguration)
class StudySourceConfigurationAdmin(admin.ModelAdmin):
    list_display = ('study', 'source_type', 'status')
    readonly_fields = ('study',)
    can_delete = False
    fields = ('study', 'source_type', 'status', 'requested_data_types', 'configuration', 'consent_template_html')
    formfield_overrides = {
        models.JSONField: {'form_class': PrettyJSONFormField},
    }

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == 'consent_template_html':
            kwargs['widget'] = AceWidget(mode='html', theme='monokai', width='100%', height='300px')
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def get_fields(self, request, obj=None):
        # A source type may register a config form that adds type-specific fields
        # (e.g. niimport's variant selector). Use the form's field list when present.
        config_form = DataSource.get_config_form_class_for_type(obj.source_type) if obj else None
        if config_form:
            return list(config_form.base_fields)
        return super().get_fields(request, obj)

    def get_form(self, request, obj=None, **kwargs):
        config_form = DataSource.get_config_form_class_for_type(obj.source_type) if obj else None
        if config_form:
            kwargs['form'] = config_form
        return super().get_form(request, obj, **kwargs)


class ConsentInline(admin.TabularInline):
    model = Consent
    list_display = ('participant_username', 'consent_date', 'revocation_date', 'is_complete')
    readonly_fields = (
        'participant_username',
        'participant_pseudo_id',
        'source_type',
        'data_source_info',
        'consent_text_accepted',
        'is_complete',
        'consent_date',
        'revocation_date',
    )
    fields = (
        'participant_username',
        'participant_pseudo_id',
        'source_type',
        'data_source_info',
        'is_complete',
    )
    can_delete = False
    extra = 0

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        if request.user.is_superuser and 'change_link' not in fields:
            return tuple(fields) + ('change_link',)
        return fields

    def get_readonly_fields(self, request, obj=None):
        readonly = super().get_readonly_fields(request, obj)
        if request.user.is_superuser and 'change_link' not in readonly:
            return tuple(readonly) + ('change_link',)
        return readonly

    @admin.display(description='')
    def change_link(self, obj):
        if not obj.pk:
            return '-'
        url = reverse('admin:studies_consent_change', args=[obj.pk])
        return format_html('<a href="{}">Edit</a>', url)

    @admin.display(description='Participant')
    def participant_username(self, obj):
        return obj.participant.user.username

    @admin.display(description='Participant ID')
    def participant_pseudo_id(self, obj):
        if obj.study_participant:
            return obj.study_participant.pseudo_id
        return "-"

    @admin.display(description='Data Source')
    def data_source_info(self, obj):
        if not obj.data_source:
            return "Not linked"
        source = obj.data_source.get_real_instance()
        status_color = 'green' if source.status == 'active' else 'orange'
        return format_html(
            '<span style="color: {};">{}</span> ({})',
            status_color,
            source.name,
            source.status
        )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Study)
class StudyAdmin(admin.ModelAdmin):
    form = StudyAdminForm
    list_display = ('title',)
    filter_horizontal = ('researchers',)
    inlines = [SourceConfigurationInline, ConsentInline]
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == 'study_page_html':
            kwargs['widget'] = AceWidget(mode='html', theme='monokai', width='100%', height='300px')
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        
        return qs.filter(researchers=request.user.profile)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.user = request.user
        form.base_fields['researchers'].widget.can_add_related = False
        form.base_fields['researchers'].widget.can_delete_related = False
        return form
    
    def has_add_permission(self, request):
        # Enforce single study per deployment
        if Study.objects.exists():
            return False
        return super().has_add_permission(request)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            obj.researchers.add(request.user.profile)

