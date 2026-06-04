from django import forms
from .models import SurveyDataSource
from data_sources.models.model_registration import datasourceform


@datasourceform(SurveyDataSource)
class SurveyDataSourceForm(forms.ModelForm):
    class Meta:
        model = SurveyDataSource
        fields = ['name']


