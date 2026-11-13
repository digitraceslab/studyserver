from django.test import TestCase
from django.contrib.auth.models import User
from survey.models import Survey, Question, Response, Answer
from users.models import Profile
from .models import SurveyDataSource
import uuid
import datetime


class SurveyDataSourceTimestampTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='survey_ts_user', password='testpass')
        self.profile = Profile.objects.create(user=self.user)
        self.survey = Survey.objects.create(
            name='Health Check',
            description='A simple health survey',
            need_logged_user=True,
        )
        self.question = Question.objects.create(
            survey=self.survey,
            text='How are you feeling?',
            order=1,
            required=False,
            type=Question.TEXT,
        )
        self.response = Response.objects.create(
            survey=self.survey,
            user=self.user,
            interview_uuid=str(uuid.uuid4()),
        )
        self.answer = Answer.objects.create(
            question=self.question,
            response=self.response,
            body='Good',
        )
        self.source = SurveyDataSource.objects.create(
            profile=self.profile,
            name='Survey Source',
        )

    def test_fetch_data_returns_non_empty_list(self):
        data = self.source.fetch_data(data_type='Health Check')
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_fetch_data_timestamp_is_int(self):
        data = self.source.fetch_data(data_type='Health Check')
        for row in data:
            self.assertIn('timestamp', row)
            self.assertIsInstance(row['timestamp'], int)

    def test_fetch_data_timestamp_is_not_datetime(self):
        data = self.source.fetch_data(data_type='Health Check')
        for row in data:
            self.assertNotIsInstance(row['timestamp'], datetime.datetime)

    def test_fetch_data_timestamp_equals_unix_ms(self):
        data = self.source.fetch_data(data_type='Health Check')
        # Refresh to get the auto_now_add value exactly as stored
        self.response.refresh_from_db()
        expected = int(self.response.created.timestamp() * 1000)
        for row in data:
            self.assertEqual(row['timestamp'], expected)


class SurveyDataSourceCountRowsTest(TestCase):
    """count_rows counts the rows fetch_data returns: this user's answers."""

    def setUp(self):
        self.user = User.objects.create_user(username='survey_count_user', password='testpass')
        self.profile = Profile.objects.create(user=self.user)
        self.other_user = User.objects.create_user(username='survey_count_other', password='testpass')
        self.survey = Survey.objects.create(
            name='Count Survey',
            description='A survey for counting',
            need_logged_user=True,
        )
        self.q1 = Question.objects.create(
            survey=self.survey, text='First?', order=1, required=False, type=Question.TEXT,
        )
        self.q2 = Question.objects.create(
            survey=self.survey, text='Second?', order=2, required=False, type=Question.TEXT,
        )
        # Two responses by the user: one answers both questions, one only the first.
        self.old_response = self._response(self.user, datetime.datetime(2026, 1, 10, 12, tzinfo=datetime.timezone.utc))
        Answer.objects.create(question=self.q1, response=self.old_response, body='a')
        Answer.objects.create(question=self.q2, response=self.old_response, body='b')
        self.new_response = self._response(self.user, datetime.datetime(2026, 3, 10, 12, tzinfo=datetime.timezone.utc))
        Answer.objects.create(question=self.q1, response=self.new_response, body='c')
        # Another user's answer must not be counted.
        other_response = self._response(self.other_user, datetime.datetime(2026, 2, 10, 12, tzinfo=datetime.timezone.utc))
        Answer.objects.create(question=self.q1, response=other_response, body='x')
        self.source = SurveyDataSource.objects.create(profile=self.profile, name='Survey Source')

    def _response(self, user, created):
        """Create a response by ``user`` and set its ``created`` time."""
        response = Response.objects.create(
            survey=self.survey, user=user, interview_uuid=str(uuid.uuid4()),
        )
        # created is auto_now_add; override it with an update.
        Response.objects.filter(pk=response.pk).update(created=created)
        return response

    def test_counts_user_answers(self):
        self.assertEqual(self.source.count_rows('Count Survey'), 3)

    def test_matches_fetch_data_length(self):
        rows = self.source.fetch_data(data_type='Count Survey')
        self.assertEqual(self.source.count_rows('Count Survey'), len(rows))

    def test_unknown_survey_returns_zero(self):
        self.assertEqual(self.source.count_rows('No Such Survey'), 0)

    def test_start_date_filters(self):
        start = datetime.datetime(2026, 2, 1, tzinfo=datetime.timezone.utc)
        self.assertEqual(self.source.count_rows('Count Survey', start_date=start), 1)

    def test_end_date_filters(self):
        end = datetime.datetime(2026, 2, 1, tzinfo=datetime.timezone.utc)
        self.assertEqual(self.source.count_rows('Count Survey', end_date=end), 2)

    def test_both_dates_filter(self):
        start = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        end = datetime.datetime(2026, 1, 31, tzinfo=datetime.timezone.utc)
        self.assertEqual(self.source.count_rows('Count Survey', start, end), 2)
