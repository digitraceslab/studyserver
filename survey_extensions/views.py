import logging

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render, reverse

from survey.decorators import survey_available
from survey.views import SurveyDetail

from survey_extensions.forms import ExtendedResponseForm

LOGGER = logging.getLogger(__name__)


def survey_csv_disabled(request, primary_key=None):
    """Shadow the library's /survey/csv/<pk>/ export.

    The library serves the full response matrix, including respondent
    usernames, to any logged-in user. Survey data must only leave the server
    through the pseudonymized data-source pipeline.
    """
    raise Http404


class ExtendedSurveyDetail(SurveyDetail):
    """SurveyDetail using ExtendedResponseForm, skipping steps with no visible questions."""

    @survey_available
    def get(self, request, *args, **kwargs):
        survey = kwargs.get("survey")
        step = kwargs.get("step", 0)
        if survey.template is not None and len(survey.template) > 4:
            template_name = survey.template
        else:
            if survey.is_all_in_one_page():
                template_name = "survey/one_page_survey.html"
            else:
                template_name = "survey/survey.html"
        if survey.need_logged_user and not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")

        session_key = "survey_{}".format(kwargs["id"])
        session_data = request.session.get(session_key, {})

        form = ExtendedResponseForm(survey=survey, user=request.user, step=step, session_data=session_data)

        if not survey.is_all_in_one_page() and not form.step_has_visible_questions(form.step):
            next_url = form.next_step_url()
            if next_url is not None:
                return redirect(next_url)
            if not session_data:
                # Nothing was ever answered (e.g. step 0 itself is hidden): nothing to finalize.
                return redirect(reverse("survey-list"))
            return self._finalize(survey, kwargs, request, session_key, session_data)

        categories = form.current_categories()

        asset_context = {
            # If any of the widgets of the current form has a "date" class, flatpickr will be loaded into the template
            "flatpickr": any(field.widget.attrs.get("class") == "date" for _, field in form.fields.items())
        }
        context = {
            "response_form": form,
            "survey": survey,
            "categories": categories,
            "step": step,
            "asset_context": asset_context,
        }

        return render(request, template_name, context)

    @survey_available
    def post(self, request, *args, **kwargs):
        survey = kwargs.get("survey")
        if survey.need_logged_user and not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")

        session_key = "survey_{}".format(kwargs["id"])
        session_data = request.session.get(session_key, {})

        form = ExtendedResponseForm(
            request.POST, survey=survey, user=request.user, step=kwargs.get("step", 0), session_data=session_data
        )
        categories = form.current_categories()

        if not survey.editable_answers and form.response is not None:
            LOGGER.info("Redirects to survey list after trying to edit non editable answer.")
            return redirect(reverse("survey-list"))
        context = {"response_form": form, "survey": survey, "categories": categories}
        if form.is_valid():
            return self.treat_valid_form(form, kwargs, request, survey)
        return self.handle_invalid_form(context, form, request, survey)

    def treat_valid_form(self, form, kwargs, request, survey):
        session_key = "survey_{}".format(kwargs["id"])
        if session_key not in request.session:
            request.session[session_key] = {}
        for key, value in list(form.cleaned_data.items()):
            request.session[session_key][key] = value
        # Step-back handling: the user may have changed a parent answer (on this
        # step or an earlier one) so that a question answered on a previous
        # visit is now hidden; drop its stale value from the session so it
        # doesn't get saved or re-shown. This scans the whole survey, not just
        # this step, since the newly-hidden question may live on another step.
        for question_id in form.survey_hidden_question_ids():
            request.session[session_key].pop(f"question_{question_id}", None)
            request.session[session_key].pop(f"question_{question_id}_other", None)
        request.session.modified = True

        next_url = form.next_step_url()
        response = None
        if survey.is_all_in_one_page():
            response = form.save()
        else:
            # when it's the last step with visible questions
            if not form.has_next_step():
                return self._finalize(survey, kwargs, request, session_key, request.session[session_key])
        # if there is a next step
        if next_url is not None:
            return redirect(next_url)
        del request.session[session_key]
        if response is None:
            return redirect(reverse("survey-list"))
        next_ = request.session.get("next", None)
        if next_ is not None:
            if "next" in request.session:
                del request.session["next"]
            return redirect(next_)
        return redirect(survey.redirect_url or "survey-confirmation", uuid=response.interview_uuid)

    def _finalize(self, survey, kwargs, request, session_key, session_data):
        """Rebuild the full response from the session, validate, save, and redirect."""
        save_form = ExtendedResponseForm(session_data, survey=survey, user=request.user)
        response = None
        if save_form.is_valid():
            response = save_form.save()
        else:
            LOGGER.warning("A step of the multipage form failed but should have been discovered before.")
        if session_key in request.session:
            del request.session[session_key]
        if response is None:
            return redirect(reverse("survey-list"))
        next_ = request.session.get("next", None)
        if next_ is not None:
            if "next" in request.session:
                del request.session["next"]
            return redirect(next_)
        return redirect(survey.redirect_url or "survey-confirmation", uuid=response.interview_uuid)
