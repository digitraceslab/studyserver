from django.urls import re_path

from survey_extensions.views import ExtendedSurveyDetail

# Anchored (fully matching) patterns so that only the survey-detail routes are
# taken over here; every other survey.urls route (list/completed/confirmation/csv)
# falls through to the library's urls.py, mounted right after this one.
urlpatterns = [
    re_path(r"^(?P<id>\d+)/$", ExtendedSurveyDetail.as_view(), name="survey-detail"),
    re_path(r"^(?P<id>\d+)-(?P<step>\d+)/$", ExtendedSurveyDetail.as_view(), name="survey-detail-step"),
]
