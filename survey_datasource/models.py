from django.db import models
from django.utils.timezone import datetime, timezone
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

    def show_links(self):
        """Show links to all surveys this user has not yet filled."""
        links = []
        surveys = Survey.objects.all()
        for survey in surveys:
            responses = Response.objects.filter(
                survey = survey,
                user = self.profile.user
            )
            if not responses.exists():
                survey_name = survey.name if survey.name else f"Survey {survey.id}"
                links.append((f"/survey/{survey.id}/", f"Go to {survey_name}"))
        return links

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
    
    def fetch_data(self, data_type='', timestamp=0, limit=1000):
        """Fetch responses for the specified survey using a timestamp cursor.

        Returns rows whose ``timestamp`` (Unix ms derived from
        ``response.created``) is >= ``timestamp``, ordered by ``created``
        ascending. The soft-limit rule is applied: up to ``limit`` rows are
        returned, extended to include all rows sharing the last timestamp so the
        caller can advance its cursor to ``last_timestamp + 1`` safely.
        """
        try:
            survey = Survey.objects.get(name=data_type)
            # Convert Unix-ms cursor to a timezone-aware datetime for the ORM filter
            cursor_dt = datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc)
            responses = Response.objects.filter(
                survey=survey,
                user=self.profile.user,
                created__gte=cursor_dt,
            ).order_by('created')

            data = []
            for response in responses:
                row_ts = int(response.created.timestamp() * 1000)
                for answer in response.answers.all():
                    data.append({
                        'survey': survey.name,
                        'question': answer.question.text,
                        'answer': answer.body,
                        # timestamp is canonically Unix time in milliseconds across all sources.
                        'timestamp': row_ts,
                    })

            # Apply soft limit
            if len(data) <= int(limit):
                return data

            batch = data[:int(limit)]
            last_ts_val = batch[-1]['timestamp']
            # Extend batch to include all rows sharing the last timestamp
            extended = [r for r in batch if r['timestamp'] != last_ts_val]
            extended += [r for r in data if r['timestamp'] == last_ts_val]
            return extended

        except Survey.DoesNotExist:
            return []
            

