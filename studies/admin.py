import json
from django.contrib import admin
from django.db import models
from django import forms
from django.utils.html import format_html
from django.urls import reverse
from django_ace import AceWidget
from .models import Study, Consent, StudyParticipant, StudySourceConfiguration, StudyAsset
from .forms import StudyAdminForm, SourceConfigurationInlineForm, StudySourceConfigurationForm
from data_sources.models import DataSource


class PrettyJSONFormField(forms.JSONField):
    widget = AceWidget(mode='json', theme='monokai', width='100%', height='300px')

    def prepare_value(self, value):
        if isinstance(value, (dict, list)):
            return json.dumps(value, indent=2)
        return super().prepare_value(value)

def _consent_summary(study_participant):
    """(required_complete, required_total, optional_complete, optional_total) for a participant."""
    consents = study_participant.consents.filter(revocation_date__isnull=True)
    required = consents.filter(is_optional=False)
    optional = consents.filter(is_optional=True)
    return (
        required.filter(is_complete=True, data_source__status='active').count(),
        required.count(),
        optional.filter(is_complete=True).count(),
        optional.count(),
    )


class ConsentInline(admin.TabularInline):
    """Consents for a single participant, shown as rows on the StudyParticipant page.

    Deliberately omits participant username/email — participants are identified
    only by their pseudonymous id.
    """
    model = Consent
    fields = ('source_type', 'data_source_info', 'is_optional', 'is_complete', 'consent_date')
    readonly_fields = (
        'source_type',
        'data_source_info',
        'is_optional',
        'is_complete',
        'consent_date',
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
            source.status,
        )

    def has_add_permission(self, request, obj=None):
        return False


class StudyParticipantInline(admin.TabularInline):
    """Participants of a study, shown as rows on the Study page with a
    completed/total consent summary instead of the individual consents."""
    model = StudyParticipant
    fields = ('pseudo_id', 'consents_summary', 'status', 'change_link')
    readonly_fields = ('pseudo_id', 'consents_summary', 'status', 'change_link')
    can_delete = False
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description='Consents (complete / total)')
    def consents_summary(self, obj):
        rc, rt, oc, ot = _consent_summary(obj)
        return f"required {rc}/{rt}, optional {oc}/{ot}"

    @admin.display(description='Status')
    def status(self, obj):
        rc, rt, _, _ = _consent_summary(obj)
        return 'Complete' if rt and rc == rt else 'Incomplete'

    @admin.display(description='')
    def change_link(self, obj):
        if not obj.pk:
            return '-'
        url = reverse('admin:studies_studyparticipant_change', args=[obj.pk])
        return format_html('<a href="{}">View consents</a>', url)


@admin.register(StudyParticipant)
class StudyParticipantAdmin(admin.ModelAdmin):
    list_display = ('study', 'pseudo_id', 'consents_summary')
    fields = ('study', 'pseudo_id')
    readonly_fields = ('study', 'pseudo_id')
    inlines = [ConsentInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(study__researchers=request.user.profile)

    def has_add_permission(self, request):
        return False

    @admin.display(description='Consents (complete / total)')
    def consents_summary(self, obj):
        rc, rt, oc, ot = _consent_summary(obj)
        return f"required {rc}/{rt}, optional {oc}/{ot}"


@admin.register(Consent)
class ConsentAdmin(admin.ModelAdmin):
    list_display = (
        'study',
        'participant_pseudo_id',
        'source_type',
        'data_source_status',
        'is_complete',
        'consent_date'
    )
    # Fields shown to researchers — identifies the participant by pseudonymous id
    # only, never by username/email or the participant/study_participant FKs
    # (whose __str__ could expose identity).
    researcher_fields = (
        'study',
        'participant_pseudo_id',
        'source_type',
        'data_source_status',
        'is_optional',
        'is_complete',
        'consent_text_accepted',
        'consent_date',
        'revocation_date',
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs

        return qs.filter(study__researchers=request.user.profile)

    def get_fields(self, request, obj=None):
        if request.user.is_superuser:
            return super().get_fields(request, obj)
        return self.researcher_fields

    def get_readonly_fields(self, request, obj=None):
        if request.user.is_superuser:
            return ('participant', 'study', 'source_type', 'consent_date')
        return self.researcher_fields

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


class StudyAssetInline(admin.TabularInline):
    model = StudyAsset
    extra = 1
    fields = ['name', 'file']
    verbose_name = 'Study Asset'
    verbose_name_plural = 'Study Assets'


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
    form = StudySourceConfigurationForm
    list_display = ('study', 'source_type', 'status')
    readonly_fields = ()
    can_delete = False
    fields = ('source_type', 'status', 'requested_data_types', 'configuration', 'consent_template_html')
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
            return [field for field in config_form.base_fields if field != 'study']
        return super().get_fields(request, obj)

    def get_form(self, request, obj=None, **kwargs):
        config_form = DataSource.get_config_form_class_for_type(obj.source_type) if obj else None
        if config_form:
            kwargs['form'] = config_form
        return super().get_form(request, obj, **kwargs)

    def save_model(self, request, obj, form, change):
        # Standalone source configuration add/edit is scoped to the single-study setup.
        if not obj.study_id:
            obj.study = Study.objects.first()
        super().save_model(request, obj, form, change)


@admin.register(Study)
class StudyAdmin(admin.ModelAdmin):
    form = StudyAdminForm
    list_display = ('title',)
    filter_horizontal = ('researchers',)
    inlines = [StudyAssetInline, SourceConfigurationInline, StudyParticipantInline]
    
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name in (
            'study_page_html',
            'privacy_notice_html',
            'terms_of_service_html',
            'join_confirmation_html',
        ):
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

