from django.contrib import admin

from survey.actions import make_published
from survey.admin import SurveyAdmin
from survey.models import Survey


class ExtendedSurveyAdmin(SurveyAdmin):
    """SurveyAdmin without the export actions.

    The library's CSV/Tex export actions include respondent usernames; survey
    data must only leave through the pseudonymized data-source pipeline, so
    only the publish action is kept.
    """

    actions = [make_published]


# Importing survey.admin above registers the library's SurveyAdmin; replace it.
admin.site.unregister(Survey)
admin.site.register(Survey, ExtendedSurveyAdmin)
