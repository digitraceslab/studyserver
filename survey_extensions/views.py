from django.http import Http404


def survey_csv_disabled(request, primary_key=None):
    """Shadow the library's /survey/csv/<pk>/ export.

    The library serves the full response matrix, including respondent
    usernames, to any logged-in user. Survey data must only leave the server
    through the pseudonymized data-source pipeline.
    """
    raise Http404
