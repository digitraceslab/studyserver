import uuid
from django.apps import apps
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from datetime import datetime
from users.models import Profile
from data_sources.models import DataSource


def _parse_config_date(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt
    except (ValueError, TypeError):
        return None


class Study(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField()
    researchers = models.ManyToManyField(
        Profile,
        related_name='studies',
        limit_choices_to={'user_type': 'researcher'},
    )
    contact_name = models.CharField(
        max_length=200,
        blank=True,
        default='',
        help_text="Name of the contact person for this study"
    )
    contact_email = models.EmailField(
        blank=True,
        default='',
        help_text="Email of the contact person for this study"
    )

    config_url = models.URLField(max_length=500, help_text="URL for fetching study configuration")
    repo_branch = models.CharField(
        max_length=100,
        default='main',
        blank=True,
        help_text="Branch name in the config repository (default: main)"
    )

    study_page_html = models.TextField(
        blank=True,
        default='',
        help_text="Front page HTML content."
    )

    @property
    def required_data_sources(self):
        if self.pk and self.source_config_entries.exists():
            return list(self.source_config_entries.filter(status='required').values_list('source_type', flat=True))
        return []

    @property
    def optional_data_sources(self):
        if self.pk and self.source_config_entries.exists():
            return list(self.source_config_entries.filter(status='optional').values_list('source_type', flat=True))
        return []

    @property
    def raw_content_base_url(self):
        """ Convert a repo URL to its raw content base URL for some known services. """
        if not self.config_url:
            return None
        if 'github.com' in self.config_url:
            return self.config_url.replace('github.com', 'raw.githubusercontent.com') + f'/{self.repo_branch}/'
        if 'gitlab.com' in self.config_url:
            return f"{self.config_url}/-/raw/{self.repo_branch}/"

        # raw urls also should work directly
        return self.config_url

    def get_source_dates(self, source_type):
        """Return (data_start, data_end) for a source type."""
        source_configuration = self.source_config_entries.filter(
            source_type=source_type
        ).first()
        if source_configuration:
            return source_configuration.data_start, source_configuration.data_end
        return None, None

    def __str__(self):
        return self.title


class StudySourceConfiguration(models.Model):
    STATUS_CHOICES = [
        ('required', 'Required'),
        ('optional', 'Optional'),
    ]

    study = models.ForeignKey(
        Study,
        on_delete=models.CASCADE,
        related_name='source_config_entries',
    )
    source_type = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='optional')
    requested_data_types = models.TextField(
        blank=True,
        default='',
        help_text='Comma-separated list of data types to request from this source',
    )
    data_start = models.DateTimeField(
        null=True, blank=True,
        help_text="Start of the data collection period. May predate consent_date."
    )
    data_end = models.DateTimeField(
        null=True, blank=True,
        help_text="End of the data collection period. May be null for ongoing collection."
    )
    configuration = models.JSONField(
        blank=True,
        default=dict,
        help_text="Source configuration. Specific to the source type."
    )

    class Meta:
        verbose_name = 'Source Configuration'
        verbose_name_plural = 'Source Configurations'

    def __str__(self):
        return f"{self.source_type} ({self.status})"


class StudyParticipant(models.Model):
    participant = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='study_participations',
        limit_choices_to={'user_type': 'participant'}
    )
    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name='participations')
    pseudo_id = models.UUIDField(default=uuid.uuid4, editable=False)

    class Meta:
        unique_together = ('participant', 'study')

    def __str__(self):
        name = self.participant.user.username if self.participant else f"[deleted-{self.pseudo_id}]"
        return f"{name} in {self.study.title}"


class Consent(models.Model):
    participant = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consents',
        limit_choices_to={'user_type': 'participant'}
    )
    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name='consents')
    data_source = models.ForeignKey(
        DataSource, 
        on_delete=models.SET_NULL,
        related_name='consents',
        null=True,
        blank=True
    )
    study_participant = models.ForeignKey(
        StudyParticipant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consents',
    )
    source_type = models.CharField(max_length=100)
    is_optional = models.BooleanField(default=False)
    is_complete = models.BooleanField(default=False)
    consent_text_accepted = models.BooleanField(default=False)
    consent_date = models.DateTimeField(null=True, blank=True)
    data_start = models.DateTimeField(
        null=True, blank=True,
        help_text="Start of the data collection period. May predate consent_date."
    )
    revocation_date = models.DateTimeField(null=True, blank=True)
    
    def __str__(self):
        if self.participant:
            name = self.participant.user.username
        elif self.study_participant:
            name = f"[deleted-{self.study_participant.pseudo_id}]"
        else:
            name = "[deleted]"
        return f"Consent of {name} for {self.study.title}"


