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
def create_auth_token(sender, instance=None, created=False, **kwargs):
    """Give every newly created user a REST framework API token."""
    if created:
        Token.objects.create(user=instance)


class Profile(models.Model):
    """Per-user data beyond Django's User: the account type and, for
    participants, the tax ID used for remuneration.

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

    def __str__(self):
        return f"{self.user.username} - {self.user_type}"


class ProtectedIdentifier(models.Model):
    """An identifier (email, phone number, username, ...) that the participant
    wants removed from study data before researchers receive it."""
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
