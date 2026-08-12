"""Privacy hardening for django-survey-and-report.

Disables the library's CSV/Tex response exports (admin actions and the
/survey/csv/<pk>/ route), which include respondent usernames; survey data must
only leave the server through the pseudonymized data-source pipeline.

The conditional questions and "other, please specify" features used to live
here but are now implemented in the survey library itself; this app keeps the
migrations that moved that data over.
"""
