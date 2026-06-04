from django import forms
from .models import JsonUrlDataSource, AwareDataSource, NiimportDataSource
from .models.base import DataSource
from .models.model_registration import datasourceform, datasourceconfigform

# StudySourceConfiguration forms for datasource-specific configuration
from studies.models import StudySourceConfiguration


@datasourceform(JsonUrlDataSource)
class JsonUrlDataSourceForm(forms.ModelForm):
    class Meta:
        model = JsonUrlDataSource
        fields = ['name', 'url']

@datasourceform(AwareDataSource)
class AwareDataSourceForm(forms.ModelForm):
    class Meta:
        model = AwareDataSource
        fields = ['name']

@datasourceform(NiimportDataSource)
class NiimportDataSourceForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.configuration = kwargs.pop('configuration', {}) or {}
        super().__init__(*args, **kwargs)
        niimport_source_type = self.configuration.get('niimport_source_type')
        if niimport_source_type:
            self.fields['niimport_source_type'].initial = niimport_source_type
            self.fields['niimport_source_type'].disabled = True

    class Meta:
        model = NiimportDataSource
        fields = ['name', 'niimport_source_type']

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.niimport_source_type:
            instance.niimport_source_type = self.configuration.get('niimport_source_type', '')
        if commit:
            instance.save()
        return instance


@datasourceconfigform(NiimportDataSource)
class NiimportStudySourceConfigurationForm(forms.ModelForm):
    """Form for StudySourceConfiguration when source_type is NiimportDataSource."""
    niimport_source_type = forms.ChoiceField(
        choices=NiimportDataSource.NIIMPORT_SOURCE_TYPE_CHOICES,
        required=True,
        label='Portability source type',
    )
    
    class Meta:
        model = StudySourceConfiguration
        fields = ['study', 'source_type', 'status', 'requested_data_types', 'configuration', 'consent_template_html']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-populate niimport_source_type from configuration if present
        if self.instance and self.instance.configuration:
            self.fields['niimport_source_type'].initial = self.instance.configuration.get('niimport_source_type')
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        # Store niimport_source_type in configuration JSON, preserving any other keys.
        instance.configuration = {
            **(instance.configuration or {}),
            'niimport_source_type': self.cleaned_data.get('niimport_source_type'),
        }
        if commit:
            instance.save()
        return instance


class DataFilterForm(forms.Form):
    data_type = forms.ChoiceField(choices=[])
    start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, **kwargs):
        data_type_choices = kwargs.pop('data_type_choices', [])
        super().__init__(*args, **kwargs)
        self.fields['data_type'].choices = [(dt, dt.title()) for dt in data_type_choices]

