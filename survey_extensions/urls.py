from django.urls import re_path

from survey_extensions.views import survey_csv_disabled

# Anchored (fully matching) pattern so that only the csv route is taken over
# here; every other survey.urls route falls through to the library's urls.py,
# mounted right after this one. The csv route is shadowed on purpose: the
# library's export leaks usernames.
urlpatterns = [
    re_path(r"^csv/(?P<primary_key>\d+)/$", survey_csv_disabled, name="survey-result"),
]
