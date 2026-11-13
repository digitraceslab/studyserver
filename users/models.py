"""User profiles and participant identifier protection.

Defines the Profile attached to every Django User (researcher or participant),
the identifiers a participant wants scrubbed from their data, and the signal
that issues each new user an API token.
"""
from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from rest_framework.authtoken.models import Token
import random
import string


def _generate_replacement():
    """Return a random placeholder like ``<aB3dE9fG>`` that stands in for a
    protected identifier in exported data."""
    chars = random.choices(string.ascii_letters + string.digits, k=8)
    return '<' + ''.join(chars) + '>'


@receiver(post_save, sender=User)
def _create_auth_token(sender, instance=None, created=False, **kwargs):
    """Give every newly created user a REST framework API token."""
    if created:
        Token.objects.create(user=instance)


class Profile(models.Model):
    """Per-user data beyond Django's User: the account type and, for
    participants, the tax ID used for remuneration.

    Fields:
        user_type: ``"researcher"`` or ``"participant"``. Views branch on it.
        tax_id: free text, ``""`` until the participant enters one. Not
            validated or encrypted.

    Every front-end user is expected to have a Profile; see
    ``users.middleware.ProfileRequiredMiddleware`` for users without one.

    Deleting a profile also deletes its data sources through their concrete
    classes, so each source can clean up its external resources.
    """
    USER_TYPE_CHOICES = (
        ("researcher", "Researcher"),
        ("participant", "Participant"),
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES)
    tax_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Tax ID collected for remuneration.",
    )


    def delete(self, *args, **kwargs):
        """Delete each data source via its concrete class before the profile."""
        for ds in self.data_sources.all():
            ds.get_real_instance().delete()
        super().delete(*args, **kwargs)

    @property
    def email(self):
        """The user's email address, or "" if none is set (API).

        This is the public way to get a participant's (or researcher's)
        contact address. Signup stores the username, an email address, here.
        Accounts not created through signup (e.g. by createsuperuser or the
        admin) may have none. Not checked for validity or deliverability.

        ``User.email`` is the authoritative copy. Participant signup also
        stores the same value as a ProtectedIdentifier, but the participant
        may later edit or remove that, and it is not read here.
        """
        return self.user.email

    def __str__(self):
        return f"{self.user.username} - {self.user_type}"


class ProtectedIdentifier(models.Model):
    """An identifier (email, phone number, username, ...) that the participant
    wants removed from study data before researchers receive it.

    Fields:
        profile: a participant Profile (reverse name ``protected_identifiers``).
            The participant restriction applies only to admin choices.
        value: the identifier text as entered, case kept (the dashboard form
            strips surrounding whitespace; signup stores the lowercased
            email). users never matches values against data; how they are
            matched (e.g. case-insensitively) is up to the consumer.
        replacement: the placeholder that replaces ``value`` in exported data,
            a random ``<`` + 8 letters/digits + ``>`` string generated once on
            creation. Not guaranteed unique.

    ``(profile, value)`` is unique: one profile can't hold the same value twice.
    The check is an exact database comparison (case-sensitive on SQLite and
    PostgreSQL), so values differing only in case can coexist.
    """
    profile = models.ForeignKey(
        Profile,
        on_delete=models.CASCADE,
        related_name='protected_identifiers',
        limit_choices_to={'user_type': 'participant'},
    )
    value = models.CharField(max_length=255)
    replacement = models.CharField(max_length=10, default=_generate_replacement)

    class Meta:
        unique_together = ('profile', 'value')

    def __str__(self):
        return f"{self.profile.user.username}: {self.value}"
