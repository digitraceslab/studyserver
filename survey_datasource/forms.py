"""Creation form of the survey data source, registered on app ready."""

from django import forms
from .models import SurveyDataSource
from data_sources.models.model_registration import datasourceform


@datasourceform(SurveyDataSource)
class SurveyDataSourceForm(forms.ModelForm):
    """Create a survey source: name only, so ``add_data_source`` creates it
    without showing the form when completing an open consent."""

    class Meta:
        model = SurveyDataSource
        fields = ['name']


