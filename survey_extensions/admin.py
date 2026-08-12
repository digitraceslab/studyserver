from django import forms
from django.contrib import admin

from survey.actions import make_published
from survey.admin import CategoryInline, SurveyAdmin
from survey.admin import QuestionInline as BaseQuestionInline
from survey.models import Question, Survey

from survey_extensions.models import QuestionCondition, QuestionExtra


class ConditionInline(admin.StackedInline):
    model = QuestionCondition
    fk_name = "question"
    extra = 0

    def get_formset(self, request, obj=None, **kwargs):
        # `obj` is the parent Question being edited; restrict the "depends on"
        # dropdown to earlier questions of the same survey. Model.clean()
        # remains the enforcement backstop.
        formset = super().get_formset(request, obj, **kwargs)
        if obj is not None:
            depends_on_field = formset.form.base_fields.get("depends_on")
            if depends_on_field is not None:
                depends_on_field.queryset = Question.objects.filter(survey=obj.survey, order__lt=obj.order)
        return formset


class ExtraInline(admin.StackedInline):
    model = QuestionExtra
    extra = 0


class QuestionAdmin(admin.ModelAdmin):
    """Attach conditions and "other" options to a question.

    Researchers keep creating questions in the Survey admin's Question inline
    as before, then come here to attach conditions/other-text options.
    """

    list_display = ("text", "survey", "order", "type")
    list_filter = ("survey",)
    inlines = [ConditionInline, ExtraInline]


admin.site.register(Question, QuestionAdmin)


class ParentQuestionChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        text = obj.text if len(obj.text) <= 50 else obj.text[:47] + "..."
        return f"question {obj.order} – {text}"


class QuestionInlineForm(forms.ModelForm):
    """Question form with a one-row condition editor (question/operator/value)
    and an "other" checkbox, flattened from the QuestionCondition/QuestionExtra
    one-to-one rows so researchers can author everything on the Survey page
    (stock admin cannot nest those inlines here). The standalone Question page
    keeps the full editors, e.g. for customizing the "other" label."""

    OPERATOR_LABELS = [
        (QuestionCondition.OP_IN, "is one of"),
        (QuestionCondition.OP_EQ, "is equal to"),
        (QuestionCondition.OP_NE, "is not equal to"),
        (QuestionCondition.OP_LT, "is less than"),
        (QuestionCondition.OP_LE, "is at most"),
        (QuestionCondition.OP_GT, "is greater than"),
        (QuestionCondition.OP_GE, "is at least"),
    ]

    condition_question = ParentQuestionChoiceField(
        queryset=Question.objects.none(),
        required=False,
        label="Show only if",
        widget=forms.Select(attrs={"style": "width: 20em"}),
    )
    condition_operator = forms.ChoiceField(
        choices=OPERATOR_LABELS,
        required=False,
        initial=QuestionCondition.OP_IN,
        label="",
        widget=forms.Select(attrs={"style": "width: 10em"}),
    )
    condition_value = forms.CharField(
        required=False, label="", widget=forms.TextInput(attrs={"style": "width: 8em"})
    )
    other_option = forms.BooleanField(
        required=False,
        label='"Other" free-text option',
        help_text="Radio and dropdown questions only.",
    )

    class Meta:
        model = Question
        fields = "__all__"

    class Media:
        css = {"all": ("survey_extensions/admin_condition.css",)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            condition = QuestionCondition.objects.filter(question=self.instance).first()
            if condition is not None:
                self.fields["condition_question"].initial = condition.depends_on_id
                self.fields["condition_operator"].initial = condition.operator
                if condition.operator == QuestionCondition.OP_IN:
                    self.fields["condition_value"].initial = condition.choices
                elif condition.number is not None:
                    number = condition.number
                    self.fields["condition_value"].initial = int(number) if number == int(number) else number
            self.fields["other_option"].initial = QuestionExtra.objects.filter(
                question=self.instance, other_option=True
            ).exists()

    def _clean_condition(self, depends_on):
        """Validate the condition row and return QuestionCondition kwargs,
        adding form errors and returning None on problems."""
        operator = self.cleaned_data.get("condition_operator")
        value = (self.cleaned_data.get("condition_value") or "").strip()
        if not operator:
            self.add_error("condition_operator", "Select an operator.")
            return None
        if not value:
            self.add_error("condition_value", "Give a value to compare the answer against.")
            return None
        if self.instance.pk and depends_on.pk == self.instance.pk:
            self.add_error("condition_question", "A question cannot depend on itself.")
            return None
        order = self.cleaned_data.get("order")
        if order is not None and (
            depends_on.order > order
            or (depends_on.order == order and self.instance.pk and depends_on.pk > self.instance.pk)
        ):
            self.add_error("condition_question", "The parent question must come before this question.")
            return None

        kwargs = {"depends_on": depends_on, "operator": operator, "choices": "", "number": None}
        if operator == QuestionCondition.OP_IN:
            if depends_on.type not in (Question.RADIO, Question.SELECT, Question.SELECT_MULTIPLE):
                self.add_error("condition_question", "'is one of choices' needs a choice-type parent question.")
                return None
            labels = [label.strip() for label in value.split(",") if label.strip()]
            parent_choices = depends_on.get_clean_choices()
            for label in labels:
                if label not in parent_choices:
                    self.add_error("condition_value", f'"{label}" is not a choice of the parent question.')
                    return None
            kwargs["choices"] = ", ".join(labels)
        else:
            if depends_on.type not in (Question.INTEGER, Question.FLOAT):
                self.add_error("condition_question", "Numeric operators need an integer or float parent question.")
                return None
            try:
                kwargs["number"] = float(value)
            except ValueError:
                self.add_error("condition_value", f'"{value}" is not a number.')
                return None
        return kwargs

    def clean(self):
        cleaned_data = super().clean()
        self._parsed_condition = None
        depends_on = cleaned_data.get("condition_question")
        if depends_on is not None:
            self._parsed_condition = self._clean_condition(depends_on)
        if cleaned_data.get("other_option") and cleaned_data.get("type") not in (Question.RADIO, Question.SELECT):
            self.add_error("other_option", 'The "other" option is only supported on radio and dropdown questions.')
        return cleaned_data

    def save_extensions(self):
        """Sync the flattened fields to the one-to-one rows. Must run after the
        question instance has been saved (called from save_formset)."""
        if self._parsed_condition is not None:
            QuestionCondition.objects.update_or_create(question=self.instance, defaults=self._parsed_condition)
        elif self.cleaned_data.get("condition_question") is None:
            QuestionCondition.objects.filter(question=self.instance).delete()
        if self.cleaned_data.get("other_option"):
            QuestionExtra.objects.update_or_create(question=self.instance, defaults={"other_option": True})
        else:
            QuestionExtra.objects.filter(question=self.instance).delete()


class SurveyQuestionInline(BaseQuestionInline):
    form = QuestionInlineForm
    # Group the condition inputs on a single admin row.
    fields = (
        "text",
        "order",
        "required",
        "category",
        "type",
        "choices",
        ("condition_question", "condition_operator", "condition_value"),
        "other_option",
    )

    def get_formset(self, request, survey_obj, *args, **kwargs):
        formset = super().get_formset(request, survey_obj, *args, **kwargs)
        if survey_obj:
            formset.form.base_fields["condition_question"].queryset = survey_obj.questions.order_by("order", "id")
        return formset


class ExtendedSurveyAdmin(SurveyAdmin):
    inlines = [CategoryInline, SurveyQuestionInline]
    # The library's CSV/Tex export actions include respondent usernames;
    # survey data must only leave through the pseudonymized data-source
    # pipeline, so only the publish action is kept.
    actions = [make_published]

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        if formset.model is Question:
            for inline_form in formset.forms:
                if (
                    inline_form.instance.pk
                    and inline_form.cleaned_data
                    and not inline_form.cleaned_data.get("DELETE")
                ):
                    inline_form.save_extensions()


# Importing survey.admin above registers the library's SurveyAdmin; replace it.
admin.site.unregister(Survey)
admin.site.register(Survey, ExtendedSurveyAdmin)
