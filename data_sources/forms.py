from django import forms
from .models import JsonUrlDataSource, AwareDataSource, NiimportDataSource

# StudySourceConfiguration forms for datasource-specific configuration
from studies.models import StudySourceConfiguration


class JsonUrlDataSourceForm(forms.ModelForm):
    class Meta:
        model = JsonUrlDataSource
        fields = ['name', 'url']

class AwareDataSourceForm(forms.ModelForm):
    class Meta:
        model = AwareDataSource
        fields = ['name']

class NiimportDataSourceForm(forms.ModelForm):
    class Meta:
        model = NiimportDataSource
        fields = ['name', 'niimport_source_type']


class NiimportStudySourceConfigurationForm(forms.ModelForm):
    """Form for StudySourceConfiguration when source_type is NiimportDataSource."""
    niimport_source_type = forms.ChoiceField(
        choices=NiimportDataSource.NIIMPORT_SOURCE_TYPE_CHOICES,
        required=True,
        label='Portability source type',
    )
    
    class Meta:
        model = StudySourceConfiguration
        fields = ['study', 'source_type', 'status', 'requested_data_types', 'configuration']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-populate niimport_source_type from configuration if present
        if self.instance and self.instance.configuration:
            self.fields['niimport_source_type'].initial = self.instance.configuration.get('niimport_source_type')
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        # Store niimport_source_type in configuration JSON
        instance.configuration = {
            'niimport_source_type': self.cleaned_data.get('niimport_source_type')
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

