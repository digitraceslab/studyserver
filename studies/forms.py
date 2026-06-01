from django import forms
from django.apps import apps
from .models import Study, DataSource, StudySourceConfiguration


def get_data_source_type_choices():
    choices = []
    for source_type, cls in DataSource._data_source_types.items():
        label = source_type.replace('_', ' ').title()
        choices.append((source_type, label))
    return choices


class StudyAdminForm(forms.ModelForm):
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
    source_type = forms.ChoiceField(choices=[])

    class Meta:
        model = StudySourceConfiguration
        fields = ['source_type', 'status', 'requested_data_types']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = get_data_source_type_choices()
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