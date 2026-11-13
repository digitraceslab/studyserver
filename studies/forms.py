"""Study forms: admin forms for studies and sources, and participant consent forms."""

from django import forms
from django.apps import apps
from data_sources.models import DataSource

from .models import Study, StudySourceConfiguration


def _get_data_source_type_choices():
    """Return the choices for the registered data source types.

    Returns:
        ``(source_type, label)`` choices for every registered data source type.
    """
    return [
        (source_type, source_type.replace('_', ' ').title())
        for source_type in DataSource.registered_source_types()
    ]


class StudyAdminForm(forms.ModelForm):
    """Admin form for Study that stops a researcher from removing themselves.

    The caller must set ``form.user`` (the editing user) on the form class
    before validation; the ``request`` keyword is accepted but unused. When
    editing an existing study, a non-superuser must stay in ``researchers``.
    """

    class Meta:
        model = Study
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        researchers = cleaned_data.get('researchers')
        if self.instance.pk and not self.user.is_superuser:
            if researchers is None:
                raise forms.ValidationError("Researchers field cannot be empty.")
            if self.user.profile not in researchers:
                raise forms.ValidationError(
                    "You cannot remove yourself from the list of researchers."
                )
            return cleaned_data


class SourceConfigurationInlineForm(forms.ModelForm):
    """``source_type`` offers the registered types; an existing unregistered
    value is kept as an extra choice so the row can still be saved."""

    source_type = forms.ChoiceField(choices=[])

    class Meta:
        model = StudySourceConfiguration
        fields = ['source_type', 'status', 'requested_data_types']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = _get_data_source_type_choices()
        if self.instance.pk and self.instance.source_type:
            existing = self.instance.source_type
            if not any(c[0] == existing for c in choices):
                choices = [(existing, existing)] + choices
        self.fields['source_type'].choices = choices


class StudySourceConfigurationForm(forms.ModelForm):
    """Like SourceConfigurationInlineForm, plus the study and configuration fields."""

    source_type = forms.ChoiceField(choices=[])

    class Meta:
        model = StudySourceConfiguration
        fields = ['study', 'source_type', 'status', 'requested_data_types', 'configuration']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = _get_data_source_type_choices()
        if self.instance.pk and self.instance.source_type:
            existing = self.instance.source_type
            if not any(c[0] == existing for c in choices):
                choices = [(existing, existing)] + choices
        self.fields['source_type'].choices = choices


class ConsentAcceptanceForm(forms.Form):
    accept_consent = forms.BooleanField(required=True, label="I consent")


class DataSourceSelectionForm(forms.Form):
    source_id = forms.ChoiceField(choices=[], required=False, label="Select existing source")

    def __init__(self, *args, available_sources=None, **kwargs):
        super().__init__(*args, **kwargs)
        if available_sources:
            self.fields['source_id'].choices = [('', '-- Select a source --')] + [
                (source.id, source.name) for source in available_sources
            ]