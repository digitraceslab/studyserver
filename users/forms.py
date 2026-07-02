from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Profile


class CustomUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = UserCreationForm.Meta.fields + ('email',)


class ParticipantCreationForm(CustomUserCreationForm):
    """Signup form for participants. Enforces the study's allowed email
    domains when the (single) study restricts them."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from studies.models import Study

        self.study = Study.objects.first()
        if self.study and self.study.allowed_domain_list():
            self.fields['email'].required = True

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if self.study and not self.study.email_allowed(email):
            domains = ", ".join(self.study.allowed_domain_list())
            raise forms.ValidationError(
                f"Registration is restricted to email addresses in: {domains}."
            )
        return email

