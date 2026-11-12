from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path(
        "terms/",
        RedirectView.as_view(pattern_name="terms_of_service", permanent=True),
        name="terms_of_service_redirect",
    ),
    path(
        "privacy/",
        RedirectView.as_view(pattern_name="privacy_statement", permanent=True),
        name="privacy_statement_redirect",
    ),
    path(
        "terms/version/<int:pk>/",
        RedirectView.as_view(pattern_name="terms_of_service_version", permanent=True),
        name="terms_of_service_version_redirect",
    ),
    path(
        "privacy/version/<int:pk>/",
        RedirectView.as_view(pattern_name="privacy_statement_version", permanent=True),
        name="privacy_statement_version_redirect",
    ),
    path("signup/", views.signup, name="signup"),
    path("signup/researcher/", views.signup_researcher, name="signup_researcher"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("token/", views.manage_token, name="manage_token"),
    path(
        "researcher-dashboard/", views.researcher_dashboard, name="researcher_dashboard"
    ),
    path(
        "participant/<int:study_id>/<int:participant_id>/",
        views.participant_detail,
        name="participant_detail",
    ),
    path("api/data/", views.my_data_api, name="my_data_api"),
    path(
        "dashboard/protected-identifiers/",
        views.update_protected_identifiers,
        name="update_protected_identifiers",
    ),
    path(
        "dashboard/tax-id/",
        views.update_tax_id,
        name="update_tax_id",
    ),
]
