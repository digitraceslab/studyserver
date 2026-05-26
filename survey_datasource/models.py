from django.db import models
from data_sources.models import DataSource
from survey.models import Response


class SurveyDataSource(DataSource):
    """Data source that integrates with django-survey module."""
    
    display_type = "Survey"
    
    class Meta:
        verbose_name = "Survey Data Source"
        verbose_name_plural = "Survey Data Sources"

    def get_setup_url(self):
        return f"/survey_datasource/{self.id}/setup/"

    def is_complete(self):
        response_exists = Response.objects.filter(
            survey = self.survey,
            participant__in = self.profile.user
        ).exists()
