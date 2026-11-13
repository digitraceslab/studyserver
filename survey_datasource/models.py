"""Survey data source: a participant's django-survey responses as data."""

from datetime import date

from django.db import models
from django.db.models import Q
from django.utils.timezone import datetime, timezone
from data_sources.models import DataSource
from survey.models import Answer, Survey, Response


class SurveyDataSource(DataSource):
    """Data source that integrates with django-survey module.
    
    For the purposes of grouping data, we treat each survey
    as a data type and each question response as a row of data.

    Data types are survey names. No setup or confirmation: active at once.
    Does not support deletion.
    """
    SOURCE_TYPE = "survey"
    # Surveys have no setup; the open surveys listed below speak for themselves.
    ACTIVE_STATUS_TEXT = ""

    @classmethod
    def display_type_for_configuration(cls, configuration):
        """Return the display name, always "Survey".

        Args:
            configuration: Ignored.
        """
        return "Survey"

    @property
    def display_type(self):
        """Always "Survey"."""
        return self.display_type_for_configuration(self.configuration)

    class Meta:
        verbose_name = "Survey Data Source"
        verbose_name_plural = "Survey Data Sources"

    def show_links(self):
        """Show links to the open surveys this user has not yet filled.

        Open surveys the user has already filled are listed with an empty
        url, which the dashboard shows as plain text.

        A survey is open when it is published and today is within its
        publication period, the same check django-survey's survey list and
        survey_available decorator apply.

        Returns:
            A list of ``(url, text)`` tuples, one per open survey.
        """
        links = []
        today = date.today()
        surveys = Survey.objects.filter(is_published=True, publish_date__lte=today, expire_date__gte=today)
        for survey in surveys:
            responses = Response.objects.filter(
                survey = survey,
                user = self.profile.user
            )
            survey_name = survey.name if survey.name else f"Survey {survey.id}"
            if not responses.exists():
                links.append((f"/survey/{survey.id}/", f"Go to {survey_name}"))
            else:
                links.append(("", f"{survey_name} (completed)"))
        return links

    def get_data_types(self):
        """Return the surveys this participant can see or has answered, as data types.

        Includes every published survey (regardless of its publication
        period) and every survey this user has a response to. Each is named
        by ``survey.name``, or "Survey <id>" when the name is empty; such a
        fallback name does not work as a ``fetch_data`` data type.

        Returns:
            A list of survey names.
        """
        surveys = Survey.objects.filter(
            Q(is_published=True) | Q(responses__user=self.profile.user)
        ).distinct()
        data_types = []
        for survey in surveys:
            survey_name = survey.name if survey.name else f"Survey {survey.id}"
            data_types.append(survey_name)
        return data_types

    def count_rows(self, data_type='battery', start_date=None, end_date=None):
        """Return the number of rows ``fetch_data`` returns for a survey.

        Counts the answers in this source owner's responses to the survey,
        one row per answer, as in ``fetch_data``.

        Args:
            data_type: The survey name (exact match).
            start_date: Optional datetime; only responses created at or
                after it are counted. None means unbounded.
            end_date: Optional datetime; only responses created at or
                before it are counted. None means unbounded.

        Returns:
            The row count; 0 for an unknown survey name.
        """
        answers = Answer.objects.filter(
            response__survey__name=data_type,
            response__user=self.profile.user,
        )
        if start_date is not None:
            answers = answers.filter(response__created__gte=start_date)
        if end_date is not None:
            answers = answers.filter(response__created__lte=end_date)
        return answers.count()

    def fetch_data(self, data_type='', timestamp=0, limit=1000):
        """Fetch responses for the specified survey using a timestamp cursor.

        Only this source owner's responses are included. Does not check
        consent.

        Args:
            data_type: The survey name (exact match).
            timestamp: Cursor; rows with ``timestamp`` >= this are returned
                (Unix time in milliseconds).
            limit: Soft limit on the number of rows.

        Returns:
            One row dict per answer, with keys ``survey`` (name),
            ``question`` (question text), ``label`` (question label),
            ``answer`` (answer body), ``other`` (answer's other text) and
            ``timestamp`` (Unix ms of ``response.created``; all answers of
            one response share it). Rows are ordered by ``timestamp``
            ascending. At most ``limit`` rows, extended to include all rows
            sharing the last timestamp, so the caller can safely advance
            its cursor to ``last_timestamp + 1``. [] for an unknown survey
            name.
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
                        'label': answer.question.label,
                        'answer': answer.body,
                        'other': answer.other_text,
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
            

