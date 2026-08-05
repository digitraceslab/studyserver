from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from survey.models import Answer, Question, Response, Survey

from survey_extensions.models import QuestionCondition
from users.models import Profile


def make_survey(**kwargs):
    defaults = {
        "name": "Test survey",
        "description": "desc",
        "need_logged_user": False,
    }
    defaults.update(kwargs)
    return Survey.objects.create(**defaults)


def make_question(survey, qtype, order, choices="", required=True, text="Question", category=None):
    return Question.objects.create(
        survey=survey,
        text=text,
        order=order,
        required=required,
        type=qtype,
        choices=choices,
        category=category,
    )


def make_condition(question, depends_on, operator, choices="", number=None):
    condition = QuestionCondition(
        question=question, depends_on=depends_on, operator=operator, choices=choices, number=number
    )
    condition.full_clean()
    condition.save()
    return condition


def set_session(client, key, value):
    session = client.session
    session[key] = value
    session.save()


class ByQuestionStepSkippingTests(TestCase):
    """3 steps: q0 unconditional radio, q1 conditional child of q0, q2 unconditional trailing."""

    def setUp(self):
        self.survey = make_survey(display_method=Survey.BY_QUESTION)
        self.q0 = make_question(self.survey, Question.RADIO, order=0, choices="Yes,No", text="Traveled?")
        self.q1 = make_question(self.survey, Question.TEXT, order=1, text="How many trips?")
        make_condition(self.q1, self.q0, QuestionCondition.OP_IN, choices="Yes")
        self.q2 = make_question(self.survey, Question.TEXT, order=2, required=False, text="Anything else?")
        self.session_key = f"survey_{self.survey.id}"

    def test_get_on_hidden_child_step_redirects_past_it(self):
        set_session(self.client, self.session_key, {f"question_{self.q0.pk}": "no"})
        url = reverse("survey-detail-step", kwargs={"id": self.survey.id, "step": 1})
        response = self.client.get(url)
        expected = reverse("survey-detail-step", kwargs={"id": self.survey.id, "step": 2})
        self.assertRedirects(response, expected, fetch_redirect_response=False)

    def test_get_on_visible_child_step_renders_normally(self):
        set_session(self.client, self.session_key, {f"question_{self.q0.pk}": "yes"})
        url = reverse("survey-detail-step", kwargs={"id": self.survey.id, "step": 1})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_post_answering_parent_skips_hidden_child_step(self):
        url = reverse("survey-detail-step", kwargs={"id": self.survey.id, "step": 0})
        response = self.client.post(url, {f"question_{self.q0.pk}": "no"})
        expected = reverse("survey-detail-step", kwargs={"id": self.survey.id, "step": 2})
        self.assertRedirects(response, expected, fetch_redirect_response=False)


class AllTrailingStepsHiddenFinalizesTests(TestCase):
    """2 steps: q0 unconditional radio, q1 conditional child of q0 (the only other step)."""

    def setUp(self):
        self.survey = make_survey(display_method=Survey.BY_QUESTION)
        self.q0 = make_question(self.survey, Question.RADIO, order=0, choices="Yes,No", text="Traveled?")
        self.q1 = make_question(self.survey, Question.TEXT, order=1, text="How many trips?")
        make_condition(self.q1, self.q0, QuestionCondition.OP_IN, choices="Yes")
        self.session_key = f"survey_{self.survey.id}"

    def test_submission_finalizes_when_all_trailing_steps_hidden(self):
        url = reverse("survey-detail-step", kwargs={"id": self.survey.id, "step": 0})
        response = self.client.post(url, {f"question_{self.q0.pk}": "no"})

        self.assertEqual(Response.objects.count(), 1)
        saved = Response.objects.get()
        self.assertNotIn(self.session_key, self.client.session)
        self.assertRedirects(
            response,
            reverse("survey-confirmation", kwargs={"uuid": saved.interview_uuid}),
            fetch_redirect_response=False,
        )
        self.assertEqual(Answer.objects.filter(response=saved, question=self.q0).count(), 1)
        self.assertEqual(Answer.objects.filter(response=saved, question=self.q1).count(), 0)


class ByCategorySkippedCategoryTests(TestCase):
    """3 categories: A unconditional, B conditional on A (hidden), C unconditional trailing."""

    def setUp(self):
        from survey.models import Category

        self.survey = make_survey(display_method=Survey.BY_CATEGORY)
        self.cat_a = Category.objects.create(survey=self.survey, name="A", order=0)
        self.cat_b = Category.objects.create(survey=self.survey, name="B", order=1)
        self.cat_c = Category.objects.create(survey=self.survey, name="C", order=2)

        self.q_a = make_question(self.survey, Question.RADIO, order=0, choices="Yes,No", text="Qa", category=self.cat_a)
        self.q_b = make_question(self.survey, Question.TEXT, order=1, text="Qb", category=self.cat_b)
        make_condition(self.q_b, self.q_a, QuestionCondition.OP_IN, choices="Yes")
        self.q_c = make_question(self.survey, Question.TEXT, order=2, text="Qc", category=self.cat_c)
        self.session_key = f"survey_{self.survey.id}"

    def test_get_on_fully_hidden_category_step_redirects_past_it(self):
        set_session(self.client, self.session_key, {f"question_{self.q_a.pk}": "no"})
        url = reverse("survey-detail-step", kwargs={"id": self.survey.id, "step": 1})
        response = self.client.get(url)
        expected = reverse("survey-detail-step", kwargs={"id": self.survey.id, "step": 2})
        self.assertRedirects(response, expected, fetch_redirect_response=False)

    def test_post_answering_parent_skips_fully_hidden_category(self):
        url = reverse("survey-detail-step", kwargs={"id": self.survey.id, "step": 0})
        response = self.client.post(url, {f"question_{self.q_a.pk}": "no"})
        expected = reverse("survey-detail-step", kwargs={"id": self.survey.id, "step": 2})
        self.assertRedirects(response, expected, fetch_redirect_response=False)


class StepBackPurgesStaleAnswerTests(TestCase):
    """2 steps: q_p (parent radio), q_c (child, visible only when q_p == 'yes')."""

    def setUp(self):
        self.survey = make_survey(display_method=Survey.BY_QUESTION, need_logged_user=True, editable_answers=True)
        self.q_p = make_question(self.survey, Question.RADIO, order=0, choices="Yes,No", text="Parent?")
        self.q_c = make_question(self.survey, Question.TEXT, order=1, text="Child")
        make_condition(self.q_c, self.q_p, QuestionCondition.OP_IN, choices="Yes")
        self.session_key = f"survey_{self.survey.id}"

        self.user = User.objects.create_user(username="participant", password="testpass")
        Profile.objects.create(user=self.user)
        self.client.login(username="participant", password="testpass")

    def test_step_back_purges_stale_child_answer(self):
        step0_url = reverse("survey-detail-step", kwargs={"id": self.survey.id, "step": 0})
        step1_url = reverse("survey-detail-step", kwargs={"id": self.survey.id, "step": 1})

        # Answer parent "yes" -> child becomes visible.
        response = self.client.post(step0_url, {f"question_{self.q_p.pk}": "yes"})
        self.assertRedirects(response, step1_url, fetch_redirect_response=False)

        # Answer the (now visible) child; this is the last step, so it finalizes.
        response = self.client.post(step1_url, {f"question_{self.q_c.pk}": "hello"})
        self.assertEqual(Response.objects.count(), 1)
        saved = Response.objects.get()
        self.assertEqual(Answer.objects.get(response=saved, question=self.q_p).body, "yes")
        self.assertEqual(Answer.objects.get(response=saved, question=self.q_c).body, "hello")
        self.assertNotIn(self.session_key, self.client.session)

        # Go back to step 0 and change the parent answer so the child becomes hidden.
        response = self.client.post(step0_url, {f"question_{self.q_p.pk}": "no"})

        # The whole survey is now finalized again (no visible trailing step).
        self.assertEqual(Response.objects.count(), 1)
        saved.refresh_from_db()
        self.assertEqual(Answer.objects.get(response=saved, question=self.q_p).body, "no")
        self.assertFalse(Answer.objects.filter(response=saved, question=self.q_c).exists())
        self.assertNotIn(self.session_key, self.client.session)
        self.assertRedirects(
            response,
            reverse("survey-confirmation", kwargs={"uuid": saved.interview_uuid}),
            fetch_redirect_response=False,
        )
