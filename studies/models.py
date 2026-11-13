"""Study models: the study, its sources, participants, consents and legal texts.

A deployment has one Study (views use ``Study.objects.first()``). A participant
joins by getting a StudyParticipant row; participation is defined by unrevoked
Consent rows (``revocation_date`` is null), one per StudySourceConfiguration.
PrivacyNotice, TermsOfService and InfoSheet are versioned: saving inserts a new
row and the highest pk is current.
"""

import re
import uuid
from datetime import datetime

from django.apps import apps
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from data_sources.models import DataSource
from users.models import Profile


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
    """The study run by this deployment, with its page content and join rules.

    Only one study per deployment is supported. ``researchers`` are the
    Profiles allowed to use the researcher data API and edit the study in the
    admin. Participants join through StudyParticipant (``participations``);
    the sources they are asked for are ``source_config_entries``.
    """

    title = models.CharField(max_length=200)
    short_description = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Short subtitle shown in the site header next to the study title.",
    )
    description = models.TextField()
    researchers = models.ManyToManyField(
        Profile,
        related_name="studies",
        limit_choices_to={"user_type": "researcher"},
    )
    contact_name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Name of the contact person for this study",
    )
    contact_email = models.EmailField(
        blank=True, default="", help_text="Email of the contact person for this study"
    )

    study_page_html = models.TextField(
        blank=True, default="", help_text="Front page HTML content."
    )

    join_confirmation_html = models.TextField(
        blank=True,
        default="",
        help_text="HTML shown on the study-wide confirmation page when a participant clicks "
        "Join, before any participation is created.",
    )

    registration_pending_html = models.TextField(
        blank=True,
        default="",
        help_text="HTML shown to a participant after they register, while awaiting "
        "researcher approval. Typically explains that their registration has been "
        "noted and researchers will contact them by email.",
    )

    custom_css = models.TextField(
        blank=True,
        default="",
        help_text="Custom CSS applied on all pages (mainly the dashboard).",
    )

    collect_tax_id = models.BooleanField(
        default=False,
        help_text="Whether participants should be asked to provide a tax ID",
    )

    allowed_email_domains = models.TextField(
        blank=True,
        default="",
        help_text="Restrict participant email addresses to these domains. "
        "Separate multiple domains with commas, spaces, or newlines "
        "(e.g. 'example.edu, example.org'). Leave empty to allow any email address.",
    )

    max_participants = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum number of participants that can register for the study. "
        "Leave empty for no limit.",
    )

    requires_approval = models.BooleanField(
        default=True,
        help_text="Participants must be approved by a researcher before they can "
        "consent or collect data. When off, participants proceed directly after "
        "registering (approval is never granted automatically).",
    )

    class Meta:
        verbose_name = "study"
        verbose_name_plural = "study"

    def get_assets_dict(self):
        """Get all assets as a dictionary.

        Returns:
            A dictionary mapping name to URL.
        """
        assets_dict = {}
        for asset in self.assets.all():
            assets_dict[asset.name] = asset.file.url
        return assets_dict

    def allowed_domain_list(self):
        """List the allowed email domains.

        Returns:
            Allowed email domains, normalized to lowercase without a leading '@'.
        """
        return [
            domain.strip().lstrip("@").lower()
            for domain in re.split(r"[\s,]+", self.allowed_email_domains)
            if domain.strip()
        ]

    def email_allowed(self, email):
        """Check whether the email's domain is allowed.

        Returns:
            True if the email's domain is allowed (or no restriction is set).
        """
        domains = self.allowed_domain_list()
        if not domains:
            return True
        if not email or "@" not in email:
            return False
        return email.rsplit("@", 1)[1].lower() in domains

    def is_full(self):
        """Check the participant limit.

        Returns:
            True if the participant limit has been reached.
        """
        if self.max_participants is None:
            return False
        return self.participations.count() >= self.max_participants

    def __str__(self):
        return self.title


class StudyAsset(models.Model):
    """Static assets (images, etc.) for a study that can be referenced in study templates."""

    study = models.ForeignKey(
        Study,
        on_delete=models.CASCADE,
        related_name="assets",
    )
    name = models.CharField(
        max_length=255,
        help_text="Asset filename (e.g., 'study_logo.png'). Use this name to reference the asset in your study HTML.",
    )
    file = models.FileField(
        upload_to="study_assets/", help_text="Upload the asset file. Images, PDFs, etc."
    )

    class Meta:
        unique_together = ("study", "name")
        verbose_name = "Study Asset"
        verbose_name_plural = "Study Assets"

    def __str__(self):
        return f"{self.study.title} - {self.name}"


class StudySourceConfiguration(models.Model):
    """A data source the study asks participants for, required or optional.

    ``source_type`` is a registered DataSource SOURCE_TYPE slug. Each approved
    participant gets one Consent per configuration; when the participant
    accepts it, the consent copies (snapshots) ``requested_data_types``,
    ``data_start``, ``data_end`` and ``configuration``, so later edits here do
    not change consents already given.
    """

    STATUS_CHOICES = [
        ("required", "Required"),
        ("optional", "Optional"),
    ]

    study = models.ForeignKey(
        Study,
        on_delete=models.CASCADE,
        related_name="source_config_entries",
    )
    source_type = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="optional")
    requested_data_types = models.TextField(
        blank=True,
        default="",
        help_text="Comma-separated list of data types to request from this source",
    )
    data_start = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Start of the data collection period. May predate consent_date.",
    )
    data_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text="End of the data collection period. May be null for ongoing collection.",
    )
    configuration = models.JSONField(
        blank=True,
        default=dict,
        help_text="Source configuration. Specific to the source type.",
    )
    consent_template_html = models.TextField(
        blank=True,
        default="",
        help_text="Consent text shown to the participant for this source. "
        "Overrides the repo-fetched consent_<source_type>.html when set.",
    )
    show_separate_consent_page = models.BooleanField(
        default=True,
        help_text="Show a separate consent page for this source. Turn off when "
        "consent for this source is already covered on the join page.",
    )

    class Meta:
        verbose_name = "Source Configuration"
        verbose_name_plural = "Source Configurations"

    def __str__(self):
        return f"{self.source_type} ({self.status})"

    def get_data_source_class(self):
        """Get the data source class for ``source_type``.

        Returns:
            The class, or None if ``source_type`` is not registered.
        """
        return DataSource.get_class_for_type(self.source_type)


class StudyParticipant(models.Model):
    """A participant's registration in the study, with a pseudonymous id.

    One row per (participant, study); rejoining reuses it, keeping
    ``pseudo_id`` and ``researcher_approved``. ``pseudo_id`` is the only
    participant identifier researchers see, including in the data API
    (``participant_id``). Withdrawing revokes consents but keeps this row.
    ``participant`` becomes null when the user's profile is deleted.
    """

    participant = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="study_participations",
        limit_choices_to={"user_type": "participant"},
    )
    study = models.ForeignKey(
        Study, on_delete=models.CASCADE, related_name="participations"
    )
    pseudo_id = models.UUIDField(default=uuid.uuid4, editable=False)
    researcher_approved = models.BooleanField(
        default=False,
        help_text="A researcher has approved this participant. Until approved, the "
        "participant cannot consent or collect data.",
    )

    class Meta:
        unique_together = ("participant", "study")

    def __str__(self):
        # Identify participants by their pseudonymous id only, never by username,
        # so researcher-facing admin pages don't expose participant identities.
        return f"Participant {self.pseudo_id} in {self.study.title}"


class VersionedText(models.Model):
    """Abstract base for legal text that is versioned instead of overwritten.

    Saving an instance never updates the existing row: each save inserts a new
    row with a new pk, so every past version is preserved and remains
    addressable by its pk. The current version is the one with the highest pk.
    """

    text = models.TextField(blank=True, default="")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["-pk"]

    def save(self, *args, **kwargs):
        # Force an insert so editing-and-saving creates a new version rather
        # than mutating the existing one.
        self.pk = None
        self._state.adding = True
        super().save(*args, **kwargs)

    @classmethod
    def current(cls):
        """Return the latest version.

        Returns:
            The latest version (highest pk), or None if none exist.
        """
        return cls.objects.order_by("-pk").first()

    def __str__(self):
        return f"{type(self).__name__} v{self.pk}"


class PrivacyNotice(VersionedText):
    class Meta(VersionedText.Meta):
        verbose_name = "Privacy Notice"
        verbose_name_plural = "Privacy Notices"


class TermsOfService(VersionedText):
    class Meta(VersionedText.Meta):
        verbose_name = "Terms of Service"
        verbose_name_plural = "Terms of Service"


class InfoSheet(VersionedText):
    class Meta(VersionedText.Meta):
        verbose_name = "Information Sheet"
        verbose_name_plural = "Information Sheets"


class Consent(models.Model):
    """A participant's consent to share one source with the study.

    Lifecycle: created empty for an approved participant; ``accept_configuration``
    snapshots the source configuration when the participant accepts the text;
    ``complete_with_source`` links the DataSource and sets ``is_complete``.
    Revoking sets ``revocation_date`` (the row is kept). The data API serves
    only complete, unrevoked consents whose data source is active, limited to
    the snapshotted ``requested_data_types`` (empty means all types) and the
    ``data_start``..``data_end`` window.
    """

    participant = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consents",
        limit_choices_to={"user_type": "participant"},
    )
    study = models.ForeignKey(Study, on_delete=models.CASCADE, related_name="consents")
    source_configuration = models.ForeignKey(
        StudySourceConfiguration,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consents",
    )
    data_source = models.ForeignKey(
        DataSource,
        on_delete=models.SET_NULL,
        related_name="consents",
        null=True,
        blank=True,
    )
    study_participant = models.ForeignKey(
        StudyParticipant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consents",
    )
    source_type = models.CharField(max_length=100)
    is_optional = models.BooleanField(default=False)
    is_complete = models.BooleanField(default=False)
    consent_text_accepted = models.BooleanField(default=False)
    consent_date = models.DateTimeField(null=True, blank=True)
    data_start = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Start of the data collection period. May predate consent_date.",
    )
    data_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text="End of the data collection period. May be null for ongoing collection.",
    )
    requested_data_types = models.TextField(blank=True, default="")
    configuration = models.JSONField(blank=True, default=dict)
    revocation_date = models.DateTimeField(null=True, blank=True)

    def accept_configuration(self):
        """Snapshot the consented parameters at the binding moment (consent form submit)."""
        self.consent_text_accepted = True
        if not self.consent_date:
            self.consent_date = timezone.now()
        cfg = self.source_configuration
        if cfg:
            self.source_type = cfg.source_type
            self.configuration = dict(cfg.configuration or {})
            self.requested_data_types = cfg.requested_data_types or ""
            self.data_end = cfg.data_end
            self.data_start = cfg.data_start or self.consent_date
        else:
            self.data_start = self.data_start or self.consent_date

    def complete_with_source(self, data_source):
        """Link the data source and mark complete.

        Params are already snapshotted at acceptance.
        """
        self.data_source = data_source
        self.is_complete = True
        if not self.consent_date:
            self.consent_date = timezone.now()
        self.save()

    def __str__(self):
        # Identify the participant by pseudonymous id only (never username) so
        # researcher-facing admin pages don't expose participant identities.
        if self.study_participant:
            marker = "" if self.participant else " (deleted)"
            name = f"participant {self.study_participant.pseudo_id}{marker}"
        elif self.participant:
            name = "participant"
        else:
            name = "[deleted]"
        return f"Consent of {name} for {self.study.title}"
