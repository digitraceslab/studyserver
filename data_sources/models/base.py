import uuid
from django.apps import apps
from django.db import models
from django.core.exceptions import ValidationError
from polymorphic.models import PolymorphicModel
from users.models import Profile


class DataSource(PolymorphicModel):
    SOURCE_TYPE = None  # Subclasses must set this
    FORM_CLASS = None  # Subclasses can set this to specify a custom form for setup
    # Subclasses can set this to a StudySourceConfiguration ModelForm that adds
    # source-type-specific configuration fields to the admin (e.g. niimport's variant).
    CONFIG_FORM_CLASS = None
    _data_source_types = {}
    _data_source_forms = {}
    _data_source_config_forms = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.SOURCE_TYPE:
            DataSource._data_source_types[cls.SOURCE_TYPE] = cls
        if cls.FORM_CLASS:
            DataSource._data_source_forms[cls.SOURCE_TYPE] = cls.FORM_CLASS
        if cls.CONFIG_FORM_CLASS:
            DataSource._data_source_config_forms[cls.SOURCE_TYPE] = cls.CONFIG_FORM_CLASS

    @classmethod
    def get_class_for_type(cls, source_type):
        return cls._data_source_types.get(source_type)

    @classmethod
    def registered_source_types(cls):
        """Return the registered SOURCE_TYPE slugs (the canonical source-type identifiers)."""
        return sorted(cls._data_source_types)

    @classmethod
    def get_form_class_for_type(cls, source_type):
        return cls._data_source_forms.get(source_type)

    @classmethod
    def register_form_class(cls, source_type, form_class):
        cls._data_source_forms[source_type] = form_class

    @classmethod
    def get_config_form_class_for_type(cls, source_type):
        """Return the StudySourceConfiguration admin form for this source type, if any."""
        return cls._data_source_config_forms.get(source_type)

    @classmethod
    def register_config_form_class(cls, source_type, form_class):
        cls._data_source_config_forms[source_type] = form_class


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
    configuration = models.JSONField(blank=True, default=dict)
    data_start = models.DateField(null=True, blank=True)
    data_end = models.DateField(null=True, blank=True)
    
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
    def display_type(self):
        """Returns a user-friendly name for the data source type."""
        return "Generic Data"

    @classmethod
    def display_type_for_configuration(cls, configuration):
        source_type = cls.SOURCE_TYPE or ''
        if source_type:
            return source_type.replace('_', ' ').title()
        return "Data Source"

    @classmethod
    def display_type_for_source_type(cls, source_type, configuration=None):
        """Resolve a stored source_type slug (+ optional configuration) to a display name.

        Delegates to the target class's ``display_type_for_configuration`` (the single
        source of truth for display names). Falls back to a title-cased slug when the
        source_type is unknown.
        """
        target = cls.get_class_for_type(source_type)
        if not target:
            return source_type.replace('_', ' ').title() if isinstance(source_type, str) else str(source_type)
        return target.display_type_for_configuration(configuration or {})

    def _matches_configuration(self, configuration):
        """Return True when all provided configuration keys match this source."""
        configuration = configuration or {}
        if not configuration:
            return True
        own_configuration = self.configuration or {}
        for key, expected_value in configuration.items():
            if own_configuration.get(key) != expected_value:
                return False
        return True

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
