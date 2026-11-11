"""Tests for repo-level shared templates.

Covers the parts of the shared templates that every app relies on: the
base.html header (study title and short description from the studies
context processor) and the password change pages and links. Django's own
password change views and forms are not tested here.
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from studies.models import Consent, Study
from users.models import Profile

STUDY_TITLE = "Header Test Study"
SHORT_DESCRIPTION = "A short header subtitle"
PASSWORD = "testpass"


def create_user(username, user_type):
    """Create a user with a Profile of the given type."""
    user = User.objects.create_user(username=username, password=PASSWORD)
    profile = Profile.objects.create(user=user, user_type=user_type)
    return user, profile


class BaseHeaderTest(TestCase):
    """The base.html header shows the study title and short description."""

    def setUp(self):
        self.study = Study.objects.create(
            title=STUDY_TITLE, short_description=SHORT_DESCRIPTION
        )

    def assert_header(self, response):
        """Assert the header shows the study title and short description."""
        self.assertContains(
            response, f'<span class="brand-name">{STUDY_TITLE}</span>', html=True
        )
        self.assertContains(
            response, f'<span class="brand-sub">{SHORT_DESCRIPTION}</span>', html=True
        )

    def test_header_for_anonymous_user(self):
        response = self.client.get(reverse("terms_of_service"))
        self.assertEqual(response.status_code, 200)
        self.assert_header(response)

    def test_header_for_logged_in_user(self):
        user, profile = create_user("participant", "participant")
        Consent.objects.create(
            participant=profile, study=self.study, consent_date=timezone.now()
        )
        self.client.login(username=user.username, password=PASSWORD)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assert_header(response)


class PasswordChangeTemplatesTest(TestCase):
    """Our password change templates render and the links to them exist."""

    def setUp(self):
        self.user, self.profile = create_user("participant", "participant")
        self.client.login(username=self.user.username, password=PASSWORD)

    def test_password_change_form_renders(self):
        response = self.client.get(reverse("password_change"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/password_change_form.html")

    def test_password_change_done_renders(self):
        response = self.client.get(reverse("password_change_done"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/password_change_done.html")

    def test_home_has_change_password_link(self):
        # home.html is rendered only when no study exists.
        response = self.client.get(reverse("home"))
        self.assertTemplateUsed(response, "home.html")
        self.assertContains(response, f'href="{reverse("password_change")}"')

    def test_dashboard_has_change_password_link(self):
        study = Study.objects.create(title=STUDY_TITLE)
        Consent.objects.create(
            participant=self.profile, study=study, consent_date=timezone.now()
        )
        response = self.client.get(reverse("dashboard"))
        self.assertTemplateUsed(response, "dashboard.html")
        self.assertContains(response, f'href="{reverse("password_change")}"')

    def test_researcher_dashboard_has_change_password_link(self):
        researcher, profile = create_user("researcher", "researcher")
        study = Study.objects.create(title=STUDY_TITLE)
        study.researchers.add(profile)
        self.client.login(username=researcher.username, password=PASSWORD)
        response = self.client.get(reverse("researcher_dashboard"))
        self.assertTemplateUsed(response, "researcher_dashboard.html")
        self.assertContains(response, f'href="{reverse("password_change")}"')
