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
