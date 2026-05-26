from django.db import models
from data_sources.models import DataSource
from survey.models import Survey, Response


class SurveyDataSource(DataSource):
    """Data source that integrates with django-survey module."""
    
    display_type = "Survey"
    
    class Meta:
        verbose_name = "Survey Data Source"
        verbose_name_plural = "Survey Data Sources"

    def get_setup_url(self):
        return f"/survey_datasource/{self.id}/setup/"

    def show_link(self):
        """Show a link to the next incomplete survey for this user, if any."""
        surveys = Survey.objects.all()
        for survey in surveys:
            responses = Response.objects.filter(
                survey = survey,
                user = self.profile.user
            )
            if not responses.exists():
                survey_name = survey.name if survey.name else f"Survey {survey.id}"
                return (f"/survey_datasource/{self.id}/setup/", f"Go to {survey_name}")
        return None
