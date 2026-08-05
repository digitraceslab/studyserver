from django.core.exceptions import ValidationError
from django.test import TestCase

from survey.models import Question, Survey

from survey_extensions.conditions import evaluate
from survey_extensions.models import QuestionCondition, QuestionExtra


def make_survey(**kwargs):
    defaults = {
        "name": "Test survey",
        "description": "desc",
        "need_logged_user": False,
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


class EvaluateOperatorTests(TestCase):
    """Direct unit tests of conditions.evaluate() for every operator."""

    def test_in_operator_matches_slug(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_IN, choices="Yes")
        self.assertTrue(evaluate(condition, "yes"))

    def test_in_operator_no_match(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_IN, choices="Yes")
        self.assertFalse(evaluate(condition, "no"))

    def test_in_operator_matches_label_needing_slugify(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_IN, choices="I don't know")
        self.assertTrue(evaluate(condition, "i-dont-know"))

    def test_in_operator_select_multiple_list_intersection_true(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_IN, choices="A,B")
        self.assertTrue(evaluate(condition, ["c", "b"]))

    def test_in_operator_select_multiple_list_intersection_false(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_IN, choices="A,B")
        self.assertFalse(evaluate(condition, ["c", "d"]))

    def test_in_operator_select_multiple_stored_body_string(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_IN, choices="A,B")
        self.assertTrue(evaluate(condition, "[u'a', u'c']"))

    def test_in_operator_select_multiple_stored_body_string_no_match(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_IN, choices="A,B")
        self.assertFalse(evaluate(condition, "[u'c', u'd']"))

    def test_in_operator_missing_parent_value_is_false(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_IN, choices="Yes")
        self.assertFalse(evaluate(condition, None))

    def test_in_operator_empty_string_is_false(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_IN, choices="Yes")
        self.assertFalse(evaluate(condition, ""))

    def test_in_operator_empty_list_is_false(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_IN, choices="Yes")
        self.assertFalse(evaluate(condition, []))

    def test_eq_true(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_EQ, number=3)
        self.assertTrue(evaluate(condition, "3"))

    def test_eq_false(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_EQ, number=3)
        self.assertFalse(evaluate(condition, "4"))

    def test_ne_true(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_NE, number=3)
        self.assertTrue(evaluate(condition, "4"))

    def test_ne_false(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_NE, number=3)
        self.assertFalse(evaluate(condition, "3"))

    def test_lt_true(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_LT, number=3)
        self.assertTrue(evaluate(condition, "2"))

    def test_lt_false(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_LT, number=3)
        self.assertFalse(evaluate(condition, "3"))

    def test_le_true(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_LE, number=3)
        self.assertTrue(evaluate(condition, "3"))

    def test_le_false(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_LE, number=3)
        self.assertFalse(evaluate(condition, "4"))

    def test_gt_true(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_GT, number=3)
        self.assertTrue(evaluate(condition, "4"))

    def test_gt_false(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_GT, number=3)
        self.assertFalse(evaluate(condition, "3"))

    def test_ge_true_equal(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_GE, number=3)
        self.assertTrue(evaluate(condition, "3"))

    def test_ge_true_greater(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_GE, number=3)
        self.assertTrue(evaluate(condition, "10"))

    def test_ge_false(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_GE, number=3)
        self.assertFalse(evaluate(condition, "2"))

    def test_numeric_unparsable_value_is_false(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_GE, number=3)
        self.assertFalse(evaluate(condition, "not-a-number"))

    def test_numeric_empty_value_is_false(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_GE, number=3)
        self.assertFalse(evaluate(condition, ""))

    def test_numeric_none_value_is_false(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_GE, number=3)
        self.assertFalse(evaluate(condition, None))

    def test_numeric_missing_number_is_false(self):
        condition = QuestionCondition(operator=QuestionCondition.OP_GE, number=None)
        self.assertFalse(evaluate(condition, "5"))


class QuestionConditionCleanTests(TestCase):
    def setUp(self):
        self.survey = make_survey()
        self.other_survey = make_survey(name="Other survey")
        self.parent = make_question(self.survey, Question.RADIO, order=1, choices="Yes,No")
        self.child = make_question(self.survey, Question.TEXT, order=2)

    def test_valid_in_condition_does_not_raise(self):
        condition = QuestionCondition(
            question=self.child, depends_on=self.parent, operator=QuestionCondition.OP_IN, choices="Yes"
        )
        condition.clean()

    def test_valid_numeric_condition_does_not_raise(self):
        numeric_parent = make_question(self.survey, Question.INTEGER, order=1, text="Age")
        numeric_child = make_question(self.survey, Question.TEXT, order=2)
        condition = QuestionCondition(
            question=numeric_child, depends_on=numeric_parent, operator=QuestionCondition.OP_GE, number=3
        )
        condition.clean()

    def test_cross_survey_parent_rejected(self):
        other_parent = make_question(self.other_survey, Question.RADIO, order=1, choices="Yes,No")
        condition = QuestionCondition(
            question=self.child, depends_on=other_parent, operator=QuestionCondition.OP_IN, choices="Yes"
        )
        with self.assertRaises(ValidationError):
            condition.clean()

    def test_self_reference_rejected(self):
        condition = QuestionCondition(
            question=self.parent, depends_on=self.parent, operator=QuestionCondition.OP_IN, choices="Yes"
        )
        with self.assertRaises(ValidationError):
            condition.clean()

    def test_later_order_parent_rejected(self):
        later = make_question(self.survey, Question.RADIO, order=3, choices="Yes,No")
        condition = QuestionCondition(
            question=self.parent, depends_on=later, operator=QuestionCondition.OP_IN, choices="Yes"
        )
        with self.assertRaises(ValidationError):
            condition.clean()

    def test_in_operator_on_integer_parent_rejected(self):
        numeric_parent = make_question(self.survey, Question.INTEGER, order=1, text="Age")
        condition = QuestionCondition(
            question=self.child, depends_on=numeric_parent, operator=QuestionCondition.OP_IN, choices="Yes"
        )
        with self.assertRaises(ValidationError):
            condition.clean()

    def test_numeric_operator_on_radio_parent_rejected(self):
        condition = QuestionCondition(
            question=self.child, depends_on=self.parent, operator=QuestionCondition.OP_GE, number=3
        )
        with self.assertRaises(ValidationError):
            condition.clean()

    def test_in_operator_label_not_in_parent_choices_rejected(self):
        condition = QuestionCondition(
            question=self.child, depends_on=self.parent, operator=QuestionCondition.OP_IN, choices="Maybe"
        )
        with self.assertRaises(ValidationError):
            condition.clean()

    def test_numeric_operator_missing_number_rejected(self):
        numeric_parent = make_question(self.survey, Question.INTEGER, order=1, text="Age")
        condition = QuestionCondition(
            question=self.child, depends_on=numeric_parent, operator=QuestionCondition.OP_GE, number=None
        )
        with self.assertRaises(ValidationError):
            condition.clean()


class QuestionExtraCleanTests(TestCase):
    def setUp(self):
        self.survey = make_survey()

    def test_other_on_radio_allowed(self):
        question = make_question(self.survey, Question.RADIO, order=1, choices="Yes,No")
        extra = QuestionExtra(question=question, other_option=True)
        extra.clean()

    def test_other_on_select_allowed(self):
        question = make_question(self.survey, Question.SELECT, order=1, choices="Yes,No")
        extra = QuestionExtra(question=question, other_option=True)
        extra.clean()

    def test_other_on_select_multiple_rejected(self):
        question = make_question(self.survey, Question.SELECT_MULTIPLE, order=1, choices="Yes,No")
        extra = QuestionExtra(question=question, other_option=True)
        with self.assertRaises(ValidationError):
            extra.clean()

    def test_other_on_text_rejected(self):
        question = make_question(self.survey, Question.TEXT, order=1)
        extra = QuestionExtra(question=question, other_option=True)
        with self.assertRaises(ValidationError):
            extra.clean()

    def test_other_disabled_on_text_does_not_raise(self):
        question = make_question(self.survey, Question.TEXT, order=1)
        extra = QuestionExtra(question=question, other_option=False)
        extra.clean()
