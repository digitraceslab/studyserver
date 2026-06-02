from django import forms
from .models import SurveyDataSource
from data_sources.models.base import DataSource


class SurveyDataSourceForm(forms.ModelForm):
    class Meta:
        model = SurveyDataSource
        fields = ['name']


DataSource.register_form_class(SurveyDataSource.SOURCE_TYPE, SurveyDataSourceForm)
