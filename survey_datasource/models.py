from django.db import models
from data_sources.models import DataSource
from survey.models import Survey, Response


class SurveyDataSource(DataSource):
    """Data source that integrates with django-survey module.
    
    For the purposes of grouping data, we treat each survey
    as a data type and each question response as a row of data.
    """
    SOURCE_TYPE = "survey"

    @classmethod
    def display_type_for_configuration(cls, configuration):
        return "Survey"

    @property
    def display_type(self):
        return self.display_type_for_configuration(self.configuration)

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
                return (f"/survey/{survey.id}/", f"Go to {survey_name}")
        return None

    def get_data_types(self):
        """Return a list of available surveys as data types."""
        surveys = Survey.objects.all()
        data_types = []
        for survey in surveys:
            survey_name = survey.name if survey.name else f"Survey {survey.id}"
            data_types.append(survey_name)
        return data_types

    def count_rows(self, data_type='battery', start_date=None, end_date=None):
        """Return the number of questions in the specified survey."""
        try:
            survey = Survey.objects.get(name=data_type)
            return survey.questions.count()
        except Survey.DoesNotExist:
            return 0
    
    def fetch_data(self, data_type='', limit=None, start_date=None, end_date=None, offset=0):
        """Fetch responses for the specified survey."""
        try:
            survey = Survey.objects.get(name=data_type)
            responses = Response.objects.filter(
                survey=survey,
                user=self.profile.user
            ).order_by('created')
            if start_date:
                responses = responses.filter(created__gte=start_date)
            if end_date:
                responses = responses.filter(created__lte=end_date)
            if offset:
                responses = responses[offset:]
            if limit:
                responses = responses[:limit]
            data = []
            for response in responses:
                for answer in response.answers.all():
                    data.append({
                        'survey': survey.name,
                        'question': answer.question.text,
                        'answer': answer.body,
                        'timestamp': response.created,
                    })
            return data
        except Survey.DoesNotExist:
            return []
            

