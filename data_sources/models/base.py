import uuid
from django.apps import apps
from django.db import models
from django.core.exceptions import ValidationError
from polymorphic.models import PolymorphicModel
from users.models import Profile


class DataSource(PolymorphicModel):
    status = models.CharField(
        max_length=20,
        choices=(
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("active", "Active"),
        ),
        default='pending'
    )
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='data_sources')
    device_id = models.UUIDField(default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, help_text="A personal name for this source")
    date_added = models.DateTimeField(auto_now_add=True)
    
    config_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    oauth_state = models.CharField(max_length=100, blank=True, null=True)
    
    requires_confirmation = False
    requires_setup = False

    def save(self, *args, **kwargs):
        # Check if device_id already exists for a different user
        if self.device_id:
            existing = DataSource.objects.filter(device_id=self.device_id).exclude(id=self.id)
            if existing.exists():
                # Check if any of the existing records belong to a different user
                if existing.exclude(profile_id=self.profile_id).exists():
                    raise ValidationError(
                        "This device ID has already been claimed by another user. "
                        "Contact the administrator if you believe this is an error."
                    )
        super().save(*args, **kwargs)

    @property
    def model_name(self):
        """Returns the simple class name of the real instance."""
        return self.get_real_instance().__class__.__name__

    @property
    def display_type(self):
        """Returns a user-friendly name for the data source type."""
        return "Generic Data"

    def show_link(self):
        """Whether the data source shoudl display a link on the dashboard.
        
        Returns:
            link: None or Tuple of (url, display_text)
        """
        return None

    def get_instructions_card(self, request, consent_id=None, study_id=None):
        """HTML card shown in instructions and dashboard."""
        return None
    
    def get_setup_url(self):
        """URL to redirect to after creating the source"""
        return None

    def revoke_before_delete(self):
        """Revoke any permissions and delete the source."""
        pass

    def get_confirm_url(self):
        return None
    
    def confirm(self, request):
        """Confirm the source and download any initial data if needed."""
        return None, None
    
    def has_active_consent(self):
        Consent = apps.get_model('studies', 'Consent')
        return Consent.objects.filter(
            data_source=self,
            revocation_date__isnull=True,
            is_complete=True
        ).exists()
    
    def process(self, *args, **kwargs):
        if not self.has_active_consent():
            print(f"No active consent for {self} ({self.pk}). Skipping processing.")
            return False, "No consent found."
        # Optionally, call a hook for subclass-specific processing
        return self._process_data(*args, **kwargs)

    def _process_data(self, *args, **kwargs):
        """Override this in subclasses for actual processing logic."""
        pass

    def get_data_types(self):
        """Returns a list of available data type names for this source."""
        raise NotImplementedError("Subclasses must implement this method.")
    
    def fetch_data(self, data_type='battery', limit=None, start_date=None, end_date=None, offset=0):
        """Fetches and returns data from the source.

        Parameters:
        - data_type: name of the data table/type to fetch
        - limit: maximum number of rows to return (None means no limit)
        - start_date, end_date: optional datetime filters
        - offset: pagination offset (use with limit)
        """
        raise NotImplementedError("Subclasses must implement this method.")

    def count_rows(self, data_type='battery', start_date=None, end_date=None):
        """Return the number of rows available for the given data_type and filters.

        Subclasses should override to provide an efficient count operation.
        """
        raise NotImplementedError("Subclasses must implement this method.")

    def __str__(self):
        return f"{self.name} ({self.profile.user.username})"
