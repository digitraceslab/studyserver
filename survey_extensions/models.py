from django.core.exceptions import ValidationError
from django.db import models
from survey.models import Question


class QuestionCondition(models.Model):
    """Show a question only when a previous answer in the same survey matches."""

    OP_IN = "in"
    OP_EQ = "eq"
    OP_NE = "ne"
    OP_LT = "lt"
    OP_LE = "le"
    OP_GT = "gt"
    OP_GE = "ge"

    OPERATORS = [
        (OP_IN, "answer is one of choices"),
        (OP_EQ, "=="),
        (OP_NE, "!="),
        (OP_LT, "<"),
        (OP_LE, "<="),
        (OP_GT, ">"),
        (OP_GE, ">="),
    ]

    NUMERIC_OPERATORS = [OP_EQ, OP_NE, OP_LT, OP_LE, OP_GT, OP_GE]

    question = models.OneToOneField(
        Question, on_delete=models.CASCADE, related_name="extension_condition"
    )
    depends_on = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="dependent_conditions"
    )
    operator = models.CharField(max_length=2, choices=OPERATORS, default=OP_IN)
    choices = models.TextField(
        blank=True, help_text="Comma-separated parent choice labels, used with the 'in' operator."
    )
    number = models.FloatField(null=True, blank=True, help_text="Used with the numeric operators.")

    def clean(self):
        if self.question_id and self.depends_on_id:
            if self.question.survey_id != self.depends_on.survey_id:
                raise ValidationError("The parent question must be in the same survey.")
            if self.question_id == self.depends_on_id:
                raise ValidationError("A question cannot depend on itself.")
            if (self.depends_on.order, self.depends_on_id) >= (self.question.order, self.question_id):
                raise ValidationError("The parent question must appear before the dependent question.")

        if self.operator == self.OP_IN:
            if self.depends_on_id and self.depends_on.type not in (
                Question.RADIO,
                Question.SELECT,
                Question.SELECT_MULTIPLE,
            ):
                raise ValidationError("The 'in' operator requires a radio/select/select-multiple parent question.")
            clean_choices = self.depends_on.get_clean_choices() if self.depends_on_id else []
            for label in self._clean_choice_labels():
                if label not in clean_choices:
                    raise ValidationError(f"'{label}' is not one of the parent question's choices.")
        elif self.operator in self.NUMERIC_OPERATORS:
            if self.depends_on_id and self.depends_on.type not in (Question.INTEGER, Question.FLOAT):
                raise ValidationError("Numeric operators require an integer/float parent question.")
            if self.number is None:
                raise ValidationError("A number is required for numeric operators.")

    def _clean_choice_labels(self):
        return [label.strip() for label in self.choices.split(",") if label.strip()]

    def __str__(self):
        return f"Condition on '{self.question}' depending on '{self.depends_on}'"


class QuestionExtra(models.Model):
    """Adds an "other, please specify" free-text option to a radio/select question."""

    question = models.OneToOneField(
        Question, on_delete=models.CASCADE, related_name="extension_extra"
    )
    other_option = models.BooleanField(default=False)
    other_label = models.CharField(max_length=200, default="Other, please specify")

    def clean(self):
        if self.other_option and self.question_id and self.question.type not in (
            Question.RADIO,
            Question.SELECT,
        ):
            raise ValidationError("The 'other' option is only available on radio/select questions.")

    def __str__(self):
        return f"Extra options for '{self.question}'"
