from django import forms
from .models import SurveyDataSource


class SurveyDataSourceForm(forms.ModelForm):
    class Meta:
        model = SurveyDataSource
        fields = ['name']
