"""User signup forms.

Accounts use the email address as the username: both forms require the
username to be a valid email address allowed by the study's email domain
allowlist, and copy it to ``User.email`` on save.
"""

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.validators import validate_email
from .models import Profile

EMAIL_NOT_ALLOWED_MESSAGE = "Please sign up with an email address ending in {domains}."
EMAIL_NOT_ALLOWED_FALLBACK_MESSAGE = "Please sign up with an allowed email address."
SIGNUP_CLOSED_MESSAGE = "Signup is not open yet."
HIDDEN_EMAIL_DOMAINS = {"example.com"}


def email_not_allowed_message(study):
    """Message naming the study's allowed email domains, leaving out the
    hidden test domains; a generic message if none remain."""
    domains = [d for d in study.allowed_domain_list() if d not in HIDDEN_EMAIL_DOMAINS]
    if not domains:
        return EMAIL_NOT_ALLOWED_FALLBACK_MESSAGE
    if len(domains) == 1:
        listed = domains[0]
    else:
        listed = ", ".join(domains[:-1]) + " or " + domains[-1]
    return EMAIL_NOT_ALLOWED_MESSAGE.format(domains=listed)


class CustomUserCreationForm(UserCreationForm):
    """Signup form (used directly for researchers). The username is an email
    address allowed by the study, and is also stored as the user's email."""

    class Meta(UserCreationForm.Meta):
        model = User
        fields = UserCreationForm.Meta.fields
        labels = {'username': 'Email address'}
        help_texts = {'username': ''}

    def clean_username(self):
        """Require a valid email address allowed by the study (signup is
        closed when there is no study). Returns it lowercased."""
        from studies.models import Study

        if self.cleaned_data.get('username'):
            self.cleaned_data['username'] = self.cleaned_data['username'].strip().lower()
        username = super().clean_username()
        if username is None:
            return username
        validate_email(username)
        study = Study.objects.first()
        if study is None:
            raise forms.ValidationError(SIGNUP_CLOSED_MESSAGE)
        if not study.email_allowed(username):
            raise forms.ValidationError(email_not_allowed_message(study))
        return username

    def save(self, commit=True):
        """Save the user with ``email`` set to the username."""
        user = super().save(commit=False)
        user.email = user.username
        if commit:
            user.save()
            if hasattr(self, "save_m2m"):
                self.save_m2m()
        return user


class ParticipantCreationForm(CustomUserCreationForm):
    """Signup form for participants."""
