from django import forms
from django.urls import reverse
from django.utils.text import slugify

from survey.forms import ResponseForm
from survey.models import Answer, Question, Survey

from survey_extensions.conditions import evaluate
from survey_extensions.models import QuestionCondition, QuestionExtra


class ExtendedResponseForm(ResponseForm):
    """A ResponseForm that supports conditional questions and "other" free-text answers."""

    OTHER_SENTINEL = "__other__"

    def __init__(self, *args, session_data=None, **kwargs):
        # Answers from earlier steps of a multi-step survey, keyed by
        # "question_<pk>". Needed to evaluate conditions whose parent
        # question is not part of the current step's fields.
        self.session_data = session_data or {}
        self._visibility_cache = {}
        self._other_initial = {}
        self.hidden_question_ids = set()

        survey = kwargs.get("survey")
        self._conditions = {}
        self._extras = {}
        if survey is not None:
            for condition in QuestionCondition.objects.filter(question__survey=survey).select_related("depends_on"):
                self._conditions[condition.question_id] = condition
            for extra in QuestionExtra.objects.filter(question__survey=survey, other_option=True).select_related(
                "question"
            ):
                self._extras[extra.question_id] = extra

        # When the form is rebuilt from session data (multi-step finalize), the
        # stored answer for an "other"-enabled question is the raw free text,
        # which would fail ChoiceField validation. Map it back to the sentinel
        # plus companion field before binding.
        if args and isinstance(args[0], dict) and not hasattr(args[0], "getlist"):
            args = (self._restore_other_sentinels(args[0]),) + args[1:]
        elif isinstance(kwargs.get("data"), dict) and not hasattr(kwargs["data"], "getlist"):
            kwargs["data"] = self._restore_other_sentinels(kwargs["data"])

        super().__init__(*args, **kwargs)
        self._questions_by_id = {question.pk: question for question in self.survey.questions.all()}

    def _restore_other_sentinels(self, data):
        """Return a copy of a plain data dict with raw "other" text replaced by
        the sentinel choice and a companion text entry, so choice validation
        passes and clean() re-merges the text."""
        data = dict(data)
        for extra in self._extras.values():
            name = f"question_{extra.question_id}"
            value = data.get(name)
            if not isinstance(value, str) or value in ("", self.OTHER_SENTINEL):
                continue
            clean_slugs = {slugify(choice) for choice in extra.question.get_clean_choices()}
            if value not in clean_slugs:
                data[name] = self.OTHER_SENTINEL
                data[f"{name}_other"] = value
        return data

    def _raw_value(self, question):
        """Return the raw submitted/stored value for a question, or None."""
        name = f"question_{question.pk}"
        if self.data:
            if question.type == Question.SELECT_MULTIPLE:
                if hasattr(self.data, "getlist"):
                    values = self.data.getlist(name)
                else:
                    # Plain dict (form rebuilt from session data).
                    values = self.data.get(name)
                if values:
                    return values
            else:
                value = self.data.get(name)
                if value not in (None, ""):
                    return value
        if name in self.session_data:
            return self.session_data[name]
        return None

    def is_visible(self, question):
        """Return whether a question should be shown, cascading through its condition chain."""
        if question.pk in self._visibility_cache:
            return self._visibility_cache[question.pk]
        condition = self._conditions.get(question.pk)
        if condition is None:
            visible = True
        else:
            parent = self._questions_by_id.get(condition.depends_on_id) or condition.depends_on
            if not self.is_visible(parent):
                visible = False
            else:
                visible = evaluate(condition, self._raw_value(parent))
        self._visibility_cache[question.pk] = visible
        return visible

    def add_question(self, question, data):
        super().add_question(question, data)
        field_name = f"question_{question.pk}"
        field = self.fields[field_name]

        # The template renders the required-asterisk from this, not from
        # field.required, which is forced off for conditional questions below.
        field.show_required = question.required

        condition = self._conditions.get(question.pk)
        if condition is not None:
            # Requiredness is re-enforced in clean() once we know the question is visible.
            field.required = False
            field.widget.attrs["data-depends-on"] = f"question_{condition.depends_on_id}"
            field.widget.attrs["data-operator"] = condition.operator
            if condition.operator == QuestionCondition.OP_IN:
                slugs = ",".join(slugify(label) for label in condition._clean_choice_labels())
                field.widget.attrs["data-cond-choices"] = slugs
            elif condition.number is not None:
                field.widget.attrs["data-cond-number"] = condition.number

        extra = self._extras.get(question.pk)
        if extra is not None:
            other_field = forms.CharField(required=False, label="")
            other_field.widget.attrs["data-other-for"] = field_name
            other_field.widget.attrs["category"] = question.category.name if question.category else ""
            other_initial = self._other_initial.get(question.pk)
            if other_initial is not None:
                other_field.initial = other_initial
            self.fields[f"{field_name}_other"] = other_field

    def get_question_choices(self, question):
        choices = super().get_question_choices(question)
        extra = self._extras.get(question.pk)
        if extra is not None and choices:
            choices = tuple(choices) + ((self.OTHER_SENTINEL, extra.other_label),)
        return choices

    def get_question_initial(self, question, data):
        initial = super().get_question_initial(question, data)
        extra = self._extras.get(question.pk)
        if extra is not None and initial not in (None, "", self.OTHER_SENTINEL):
            clean_slugs = {slugify(choice) for choice in question.get_clean_choices()}
            if initial not in clean_slugs:
                # The stored body is free "other" text, not one of the choice slugs.
                self._other_initial[question.pk] = initial
                initial = self.OTHER_SENTINEL
        return initial

    def _step_question_ids(self):
        """Return the pks of the questions that are fields on this form instance."""
        ids = []
        for name in self.fields:
            if name.startswith("question_") and not name.endswith("_other"):
                try:
                    ids.append(int(name.split("_")[1]))
                except (IndexError, ValueError):
                    continue
        return ids

    def clean(self):
        cleaned_data = super().clean()
        self.hidden_question_ids = set()
        for question_id in self._step_question_ids():
            question = self._questions_by_id.get(question_id)
            if question is None:
                continue
            field_name = f"question_{question_id}"
            other_name = f"{field_name}_other"

            if not self.is_visible(question):
                self.hidden_question_ids.add(question_id)
                self.errors.pop(field_name, None)
                self.errors.pop(other_name, None)
                cleaned_data.pop(field_name, None)
                cleaned_data.pop(other_name, None)
                continue

            if (
                question.pk in self._conditions
                and question.required
                and not cleaned_data.get(field_name)
                and field_name not in self.errors
            ):
                self.add_error(field_name, "This field is required.")

            extra = self._extras.get(question_id)
            if extra is not None:
                if cleaned_data.get(field_name) == self.OTHER_SENTINEL:
                    other_text = (cleaned_data.get(other_name) or "").strip()
                    if not other_text:
                        if question.required:
                            self.add_error(field_name, "This field is required.")
                        else:
                            # Don't store the literal sentinel as the answer.
                            cleaned_data[field_name] = ""
                    else:
                        cleaned_data[field_name] = other_text
                # Always pop the companion field: ResponseForm.save() creates an
                # Answer for every "question_*" cleaned_data key (parsing the pk
                # as split("_")[1]), so leaving it in would create a duplicate
                # Answer for the same question.
                cleaned_data.pop(other_name, None)
        return cleaned_data

    def survey_hidden_question_ids(self):
        """Return the ids of every survey question currently hidden, not just the
        ones that happen to be fields on this step's form. Used by the view to
        purge stale session answers when a step-back changes a parent answer."""
        return {question.pk for question in self._questions_by_id.values() if not self.is_visible(question)}

    def save(self, commit=True):
        for question_id in self.hidden_question_ids:
            self.cleaned_data.pop(f"question_{question_id}", None)
        response = super().save(commit=commit)
        if response is not None and self.hidden_question_ids:
            # Re-edits may have left stale answers for questions that are now hidden.
            Answer.objects.filter(response=response, question_id__in=self.hidden_question_ids).delete()
        return response

    def _questions_for_step(self, step):
        """Mirror ResponseForm.add_questions' step -> question mapping."""
        if self.survey.display_method == Survey.BY_CATEGORY:
            if step == len(self.categories):
                return list(self.qs_with_no_cat)
            if 0 <= step < len(self.categories):
                return list(self.survey.questions.filter(category=self.categories[step]))
            return []
        all_questions = list(self.survey.questions.all())
        if 0 <= step < len(all_questions):
            return [all_questions[step]]
        return []

    def step_has_visible_questions(self, step):
        return any(self.is_visible(question) for question in self._questions_for_step(step))

    def has_next_step(self):
        if self.survey.is_all_in_one_page():
            return False
        step = self.step + 1
        while step < self.steps_count:
            if self.step_has_visible_questions(step):
                return True
            step += 1
        return False

    def next_step_url(self):
        if not self.has_next_step():
            return None
        step = self.step + 1
        while step < self.steps_count:
            if self.step_has_visible_questions(step):
                return reverse("survey-detail-step", kwargs={"id": self.survey.id, "step": step})
            step += 1
        return None
