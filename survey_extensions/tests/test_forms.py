import uuid

from django.contrib.auth.models import AnonymousUser, User
from django.http import QueryDict
from django.test import TestCase

from survey.models import Answer, Question, Response, Survey

from survey_extensions.forms import ExtendedResponseForm
from survey_extensions.models import QuestionCondition, QuestionExtra


def make_survey(**kwargs):
    defaults = {
        "name": "Test survey",
        "description": "desc",
        "need_logged_user": False,
        "display_method": Survey.ALL_IN_ONE_PAGE,
    }
    defaults.update(kwargs)
    return Survey.objects.create(**defaults)


def make_question(survey, qtype, order, choices="", required=True, text="Question"):
    return Question.objects.create(
        survey=survey,
        text=text,
        order=order,
        required=required,
        type=qtype,
        choices=choices,
    )


def make_condition(question, depends_on, operator, choices="", number=None):
    condition = QuestionCondition(
        question=question, depends_on=depends_on, operator=operator, choices=choices, number=number
    )
    condition.full_clean()
    condition.save()
    return condition


def make_extra(question, other_label="Other, please specify"):
    extra = QuestionExtra(question=question, other_option=True, other_label=other_label)
    extra.full_clean()
    extra.save()
    return extra


def qd(data):
    """Build a mutable QueryDict from a plain dict, supporting list values for
    multi-valued fields (mirrors what an HTML form POST produces)."""
    query_dict = QueryDict(mutable=True)
    for key, value in data.items():
        if isinstance(value, (list, tuple)):
            query_dict.setlist(key, value)
        else:
            query_dict[key] = value
    return query_dict


def store_answer(question, response, body):
    """Create an Answer with the given body while bypassing Answer.__init__'s
    choice validation, exactly as ExtendedResponseForm.save() does (body is
    assigned after construction, not passed to the constructor)."""
    answer = Answer(question=question, response=response)
    answer.body = body
    answer.save()
    return answer


class ConditionalVisibilityTests(TestCase):
    def setUp(self):
        self.survey = make_survey()
        self.q1 = make_question(self.survey, Question.RADIO, order=1, choices="Yes,No", text="Q1")
        self.q2 = make_question(self.survey, Question.TEXT, order=2, text="Q2")
        make_condition(self.q2, self.q1, QuestionCondition.OP_IN, choices="Yes")

    def test_hidden_child_produces_no_required_error(self):
        data = qd({f"question_{self.q1.pk}": "no"})
        form = ExtendedResponseForm(data, survey=self.survey, user=AnonymousUser(), step=0)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn(f"question_{self.q2.pk}", form.errors)

    def test_hidden_child_produces_no_answer_row(self):
        data = qd({f"question_{self.q1.pk}": "no"})
        form = ExtendedResponseForm(data, survey=self.survey, user=AnonymousUser(), step=0)
        self.assertTrue(form.is_valid(), form.errors)
        response = form.save()
        self.assertEqual(Answer.objects.filter(response=response, question=self.q2).count(), 0)

    def test_conditional_field_has_data_attrs(self):
        form = ExtendedResponseForm(survey=self.survey, user=AnonymousUser(), step=0)
        widget_attrs = form.fields[f"question_{self.q2.pk}"].widget.attrs
        self.assertEqual(widget_attrs["data-depends-on"], f"question_{self.q1.pk}")
        self.assertEqual(widget_attrs["data-operator"], QuestionCondition.OP_IN)
        self.assertEqual(widget_attrs["data-cond-choices"], "yes")

    def test_visible_required_conditional_left_blank_errors(self):
        data = qd({f"question_{self.q1.pk}": "yes", f"question_{self.q2.pk}": ""})
        form = ExtendedResponseForm(data, survey=self.survey, user=AnonymousUser(), step=0)
        self.assertFalse(form.is_valid())
        self.assertIn(f"question_{self.q2.pk}", form.errors)

    def test_visible_answered_conditional_is_valid(self):
        data = qd({f"question_{self.q1.pk}": "yes", f"question_{self.q2.pk}": "hello"})
        form = ExtendedResponseForm(data, survey=self.survey, user=AnonymousUser(), step=0)
        self.assertTrue(form.is_valid(), form.errors)


class CascadeVisibilityTests(TestCase):
    def setUp(self):
        self.survey = make_survey()
        self.q1 = make_question(self.survey, Question.RADIO, order=1, choices="Yes,No", text="Q1")
        self.q2 = make_question(self.survey, Question.RADIO, order=2, choices="A,B", required=False, text="Q2")
        make_condition(self.q2, self.q1, QuestionCondition.OP_IN, choices="Yes")
        self.q3 = make_question(self.survey, Question.TEXT, order=3, required=False, text="Q3")
        make_condition(self.q3, self.q2, QuestionCondition.OP_IN, choices="A")

    def test_grandchild_hidden_when_own_parent_hidden_even_if_its_own_condition_would_hold(self):
        # q1 = "no" hides q2. Craft data so that, taken in isolation, q3's own
        # condition on q2 == "a" would evaluate True -- it must still be hidden
        # because its parent (q2) is hidden.
        data = qd(
            {
                f"question_{self.q1.pk}": "no",
                f"question_{self.q2.pk}": "a",
                f"question_{self.q3.pk}": "some text",
            }
        )
        form = ExtendedResponseForm(data, survey=self.survey, user=AnonymousUser(), step=0)
        self.assertFalse(form.is_visible(self.q2))
        self.assertFalse(form.is_visible(self.q3))

    def test_grandchild_visible_when_whole_chain_matches(self):
        data = qd(
            {
                f"question_{self.q1.pk}": "yes",
                f"question_{self.q2.pk}": "a",
                f"question_{self.q3.pk}": "some text",
            }
        )
        form = ExtendedResponseForm(data, survey=self.survey, user=AnonymousUser(), step=0)
        self.assertTrue(form.is_visible(self.q2))
        self.assertTrue(form.is_visible(self.q3))


class NumericConditionTests(TestCase):
    def setUp(self):
        self.survey = make_survey()
        self.age = make_question(self.survey, Question.INTEGER, order=1, text="Age")
        self.senior = make_question(self.survey, Question.TEXT, order=2, required=False, text="Senior info")
        make_condition(self.senior, self.age, QuestionCondition.OP_GE, number=65)

    def test_ge_condition_shows_field_when_met(self):
        data = qd({f"question_{self.age.pk}": "70"})
        form = ExtendedResponseForm(data, survey=self.survey, user=AnonymousUser(), step=0)
        self.assertTrue(form.is_visible(self.senior))

    def test_ge_condition_hides_field_when_not_met(self):
        data = qd({f"question_{self.age.pk}": "10"})
        form = ExtendedResponseForm(data, survey=self.survey, user=AnonymousUser(), step=0)
        self.assertFalse(form.is_visible(self.senior))


class OtherOptionTests(TestCase):
    def setUp(self):
        self.survey = make_survey()
        self.question = make_question(self.survey, Question.RADIO, order=1, choices="Red,Blue", text="Color")
        make_extra(self.question)

    def test_other_round_trip_creates_single_answer_with_raw_text(self):
        data = qd(
            {
                f"question_{self.question.pk}": ExtendedResponseForm.OTHER_SENTINEL,
                f"question_{self.question.pk}_other": "Chartreuse",
            }
        )
        form = ExtendedResponseForm(data, survey=self.survey, user=AnonymousUser(), step=0)
        self.assertTrue(form.is_valid(), form.errors)
        response = form.save()
        answers = Answer.objects.filter(response=response, question=self.question)
        self.assertEqual(answers.count(), 1)
        self.assertEqual(answers.first().body, "Chartreuse")

    def test_other_selected_with_empty_text_on_required_question_errors(self):
        data = qd(
            {
                f"question_{self.question.pk}": ExtendedResponseForm.OTHER_SENTINEL,
                f"question_{self.question.pk}_other": "",
            }
        )
        form = ExtendedResponseForm(data, survey=self.survey, user=AnonymousUser(), step=0)
        self.assertFalse(form.is_valid())
        self.assertIn(f"question_{self.question.pk}", form.errors)

    def test_regular_choice_still_stores_normally(self):
        data = qd({f"question_{self.question.pk}": "red"})
        form = ExtendedResponseForm(data, survey=self.survey, user=AnonymousUser(), step=0)
        self.assertTrue(form.is_valid(), form.errors)
        response = form.save()
        answers = Answer.objects.filter(response=response, question=self.question)
        self.assertEqual(answers.count(), 1)
        self.assertEqual(answers.first().body, "red")

    def test_reedit_maps_stored_free_text_to_other_sentinel(self):
        user = User.objects.create_user(username="reeditor", password="testpass")
        response = Response.objects.create(survey=self.survey, user=user, interview_uuid=str(uuid.uuid4()))
        store_answer(self.question, response, "Some random text")

        form = ExtendedResponseForm(survey=self.survey, user=user, step=0)
        field = form.fields[f"question_{self.question.pk}"]
        other_field = form.fields[f"question_{self.question.pk}_other"]
        self.assertEqual(field.initial, ExtendedResponseForm.OTHER_SENTINEL)
        self.assertEqual(other_field.initial, "Some random text")

    def test_reedit_of_normal_choice_does_not_trigger_other_sentinel(self):
        user = User.objects.create_user(username="reeditor2", password="testpass")
        response = Response.objects.create(survey=self.survey, user=user, interview_uuid=str(uuid.uuid4()))
        store_answer(self.question, response, "red")

        form = ExtendedResponseForm(survey=self.survey, user=user, step=0)
        field = form.fields[f"question_{self.question.pk}"]
        self.assertEqual(field.initial, "red")
