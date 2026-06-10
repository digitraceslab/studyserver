from django.db import models

from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from rest_framework.authtoken.models import Token
import random
import string


def _generate_replacement():
    chars = random.choices(string.ascii_letters + string.digits, k=8)
    return '<' + ''.join(chars) + '>'


@receiver(post_save, sender=User)
def create_auth_token(sender, instance=None, created=False, **kwargs):
    if created:
        Token.objects.create(user=instance)


class Profile(models.Model):
    USER_TYPE_CHOICES = (
        ("researcher", "Researcher"),
        ("participant", "Participant"),
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES)


    def delete(self, *args, **kwargs):
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
