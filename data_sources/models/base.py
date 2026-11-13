"""DataSource base model: the base class every data source type extends.

``DataSource`` is a polymorphic model; each row belongs to one participant
``Profile``. Querying ``DataSource.objects`` returns base rows; call
``get_real_instance()`` to get the concrete subclass before calling the
hooks below.

Writing a subclass:

- Set ``SOURCE_TYPE`` (a unique slug). Defining the subclass registers it
  for ``get_class_for_type``. A form registered with ``@datasourceform``
  (or ``FORM_CLASS``) is needed for participants to add it.
- Implement ``get_data_types``, ``fetch_data`` and ``count_rows``.
- Optionally override the setup / confirmation / dashboard hooks
  (``requires_setup``, ``requires_confirmation``, ``get_setup_url``,
  ``confirm``, ``get_instructions_card``, ``show_links``,
  ``revoke_before_delete``, ``_process_data``) and the deletion hooks
  (``supports_deletion``, ``latest_timestamp``, ``mark_deletable``,
  ``unmark_deletable``).

Rows returned by ``fetch_data`` are dicts. Every source includes an int
``timestamp`` field in Unix milliseconds; the other field names depend on
the source type and data type.

Access control is not part of this model: methods do not check who calls
them. Views must restrict access themselves (normally to the owner
``profile``).
"""

import uuid

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models
from polymorphic.models import PolymorphicModel

from users.models import Profile


class DataSource(PolymorphicModel):
    """A participant's connection to one source of data.

    Fields shared by all types: ``status`` (pending / processing / active),
    owner ``profile``, ``device_id`` (set once the source is linked to a
    device; unique across users, see ``save``), user-given ``name``,
    ``configuration`` (copied from the consent or study source configuration
    when created), requested ``data_start`` / ``data_end`` dates, and
    ``config_token`` (a secret UUID; anyone holding it can reach the
    type's token views, see ``views.token_view_dispatcher``).
    """

    SOURCE_TYPE = None  # Subclasses must set this
    FORM_CLASS = None  # Subclasses can set this to specify a custom form for setup
    # Subclasses can set this to a StudySourceConfiguration ModelForm that adds
    # source-type-specific configuration fields to the admin (e.g. niimport's variant).
    CONFIG_FORM_CLASS = None
    # Dashboard status shown once the source is active; empty shows nothing.
    ACTIVE_STATUS_TEXT = "Setup complete: collecting data"
    _data_source_types = {}
    _data_source_forms = {}
    _data_source_config_forms = {}
    _data_source_admin_child_models = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.SOURCE_TYPE:
            DataSource._data_source_types[cls.SOURCE_TYPE] = cls
        if cls.FORM_CLASS:
            DataSource._data_source_forms[cls.SOURCE_TYPE] = cls.FORM_CLASS
        if cls.CONFIG_FORM_CLASS:
            DataSource._data_source_config_forms[cls.SOURCE_TYPE] = (
                cls.CONFIG_FORM_CLASS
            )

    @classmethod
    def get_class_for_type(cls, source_type):
        """Look up a registered subclass by its SOURCE_TYPE.

        Args:
            source_type: The SOURCE_TYPE slug.

        Returns:
            The subclass with that SOURCE_TYPE, or None.
        """
        return cls._data_source_types.get(source_type)

    @classmethod
    def registered_source_types(cls):
        """List the registered SOURCE_TYPE slugs (the canonical source-type identifiers).

        Returns:
            A new list holding every slug that ``get_class_for_type``
            resolves, sorted alphabetically (not in registration order).
        """
        return sorted(cls._data_source_types)

    @classmethod
    def get_form_class_for_type(cls, source_type):
        """Look up the participant creation form for a source type.

        Args:
            source_type: The SOURCE_TYPE slug.

        Returns:
            The registered form class, or None.
        """
        return cls._data_source_forms.get(source_type)

    @classmethod
    def register_form_class(cls, source_type, form_class):
        """Register the creation form for a source type.

        Replaces any earlier registration. Normally called through the
        ``@datasourceform`` decorator. The form must accept an optional
        ``configuration`` keyword (see ``forms.BaseDataSourceForm``).

        Args:
            source_type: The SOURCE_TYPE slug.
            form_class: The form class to use as the creation form.
        """
        cls._data_source_forms[source_type] = form_class

    @classmethod
    def get_config_form_class_for_type(cls, source_type):
        """Look up the StudySourceConfiguration admin form for a source type.

        Args:
            source_type: The SOURCE_TYPE slug.

        Returns:
            The registered form class, or None.
        """
        return cls._data_source_config_forms.get(source_type)

    @classmethod
    def register_config_form_class(cls, source_type, form_class):
        """Register the admin StudySourceConfiguration form for a source type.

        Replaces any earlier registration. Normally called through the
        ``@datasourceconfigform`` decorator.

        Args:
            source_type: The SOURCE_TYPE slug.
            form_class: The form class to register.
        """
        cls._data_source_config_forms[source_type] = form_class

    @classmethod
    def register_admin_child_model(cls, model_class):
        """Register a model class for PolymorphicParentModelAdmin child routing.

        Registering the same class twice has no further effect.

        Args:
            model_class: A concrete DataSource subclass.

        Raises:
            TypeError: If ``model_class`` is not a DataSource subclass.
            ValueError: If it is DataSource itself or an abstract model.
        """
        if not isinstance(model_class, type) or not issubclass(model_class, DataSource):
            raise TypeError("admin child model must be a DataSource subclass")
        if model_class is DataSource:
            raise ValueError("base DataSource cannot be registered as a child model")
        if model_class._meta.abstract:
            raise ValueError("abstract DataSource models cannot be registered")
        if model_class not in cls._data_source_admin_child_models:
            cls._data_source_admin_child_models.append(model_class)

    @classmethod
    def get_registered_admin_child_models(cls):
        """Return the registered PolymorphicParentModelAdmin child models as a tuple."""
        return tuple(cls._data_source_admin_child_models)

    status = models.CharField(
        max_length=20,
        choices=(
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("active", "Active"),
        ),
        default="pending",
    )
    profile = models.ForeignKey(
        Profile, on_delete=models.CASCADE, related_name="data_sources"
    )
    device_id = models.UUIDField(null=True, blank=True, default=None, editable=False)
    name = models.CharField(max_length=100, help_text="A personal name for this source")
    date_added = models.DateTimeField(auto_now_add=True)
    configuration = models.JSONField(blank=True, default=dict)
    data_start = models.DateField(null=True, blank=True)
    data_end = models.DateField(null=True, blank=True)

    config_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    oauth_state = models.CharField(max_length=100, blank=True, null=True)

    # A new source stays "pending" when either flag is True; otherwise
    # add_data_source makes it "active" at once. requires_setup also makes
    # add_data_source redirect to get_setup_url(). requires_confirmation
    # means the participant must later call the confirm_data_source view.
    requires_confirmation = False
    requires_setup = False

    def save(self, *args, **kwargs):
        """Save the source, refusing a device already claimed by another user.

        The same profile may have several sources with one device_id.

        Raises:
            ValidationError: If ``device_id`` is set and another DataSource
                owned by a different profile has the same ``device_id``.
        """
        # Check if device_id already exists for a different user
        if self.device_id:
            existing = DataSource.objects.filter(device_id=self.device_id).exclude(
                id=self.id
            )
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
        """User-friendly name for the data source type ("Generic Data" by default)."""
        return "Generic Data"

    @classmethod
    def display_type_for_configuration(cls, configuration):
        """Return the display name of this type for a configuration dict.

        Subclasses whose name depends on the configuration (niimport's
        variant) read it, others ignore it. The default is the title-cased
        SOURCE_TYPE, or "Data Source" when the class has none.

        Args:
            configuration: A StudySourceConfiguration's or consent's
                configuration JSON.

        Returns:
            The display name.
        """
        source_type = cls.SOURCE_TYPE or ""
        if source_type:
            return source_type.replace("_", " ").title()
        return "Data Source"

    @classmethod
    def display_type_for_source_type(cls, source_type, configuration=None):
        """Resolve a stored source_type slug (+ optional configuration) to a display name.

        Delegates to the target class's ``display_type_for_configuration`` (the single
        source of truth for display names). Falls back to a title-cased slug when the
        source_type is unknown.

        Args:
            source_type: The stored SOURCE_TYPE slug.
            configuration: Optional configuration dict passed on to the class.

        Returns:
            The display name.
        """
        target = cls.get_class_for_type(source_type)
        if not target:
            return (
                source_type.replace("_", " ").title()
                if isinstance(source_type, str)
                else str(source_type)
            )
        return target.display_type_for_configuration(configuration or {})

    def matches_configuration(self, configuration):
        """Check whether this source's configuration matches the given one.

        An empty or None ``configuration`` matches any source. A key missing
        from the source's configuration counts as None.

        Args:
            configuration: Dict of expected configuration values.

        Returns:
            True when every key of ``configuration`` equals this source's value.
        """
        configuration = configuration or {}
        if not configuration:
            return True
        own_configuration = self.configuration or {}
        for key, expected_value in configuration.items():
            if own_configuration.get(key) != expected_value:
                return False
        return True

    def show_links(self):
        """Links the data source should display on the dashboard.

        Returns:
            List of (url, display_text) tuples. An empty url is shown as
            plain text.
        """
        return []

    def get_instructions_card(self, request, consent_id=None, study_id=None):
        """Setup instructions shown by the ``instructions`` view and dashboard.

        The default returns None, which the ``instructions`` view cannot
        unpack (it raises TypeError, a 500), so only types with
        ``requires_setup`` that point ``get_setup_url`` at that view should
        rely on it.

        Args:
            request: The current HTTP request.
            consent_id: Id of the consent the source is created from, if any.
            study_id: Id of the study, if any.

        Returns:
            A ``(context, template_name)`` tuple: the template is rendered
            with ``context`` inside ``data_sources/instructions_wrapper.html``.
        """
        return None

    def get_setup_url(self):
        """URL to redirect the participant to right after creating the source.

        Called by ``add_data_source`` only when ``requires_setup`` is True;
        the view appends ``?consent_id=`` when creating from a consent. May
        raise; the view then shows the error and deletes the new source.

        Returns:
            The URL; the default is None.
        """
        return None

    def revoke_before_delete(self):
        """Revoke access at the external source before the row is deleted.

        Called by ``delete_data_source`` just before deleting; it does not
        delete the DataSource row itself. Default no-op.
        """
        pass

    def supports_deletion(self):
        """Whether researchers can mark this source's data deletable. Default: no."""
        return False

    def latest_timestamp(self, data_type, start_date=None, end_date=None):
        """Newest row timestamp physically available at the source.

        Overridden by sources that support deletion.

        Args:
            data_type: Data type name, one of ``get_data_types()``.
            start_date: Optional lower bound of the range considered.
            end_date: Optional upper bound of the range considered.

        Returns:
            The newest timestamp (Unix time in milliseconds), or None when
            unknown or there is no data.
        """
        return None

    def mark_deletable(self, data_type, through):
        """Signal to the underlying source that downloaded data may be deleted.

        Default is a no-op (e.g. survey/json_url do not support deletion).
        Subclasses that own deletable storage override this. May raise on failure;
        callers should not advance the deletable watermark if it raises.

        Args:
            data_type: Data type name whose data may be deleted.
            through: Unix-ms timestamp up to which the data has been
                downloaded and may be deleted.
        """
        return None

    def unmark_deletable(self):
        """Cancel any pending deletion requests previously created by
        ``mark_deletable``. Data already deleted at the source cannot be restored.

        Default is a no-op. Subclasses that own deletable storage override this.
        May raise on failure; callers should not clear the deletable watermark
        if it raises.
        """
        return None

    def get_confirm_url(self):
        """URL of the ``confirm_data_source`` view for this source.

        Used by types with ``requires_confirmation``.

        Returns:
            The URL; the default is None.
        """
        return None

    def confirm(self, request):
        """Confirm that setup finished (e.g. the device has sent data).

        Called by the ``confirm_data_source`` view. Implementations normally
        set ``status`` to "active" on success. The default returns
        ``(None, None)``, which the view treats as a failure with an empty
        message.

        Args:
            request: The current HTTP request.

        Returns:
            A ``(success, message)`` tuple; the view shows ``message`` as a
            success or error message.
        """
        return None, None

    def has_active_consent(self):
        """True when a completed, unrevoked study Consent links to this source."""
        Consent = apps.get_model("studies", "Consent")
        return Consent.objects.filter(
            data_source=self, revocation_date__isnull=True, is_complete=True
        ).exists()

    def process(self, *args, **kwargs):
        """Run periodic background work for this source.

        Called by the ``process_data_sources`` Celery task for every source.
        Does nothing when ``has_active_consent()`` is False.

        Returns:
            ``(False, "No consent found.")`` without an active consent;
            otherwise whatever the subclass's ``_process_data`` returns
            (None for the built-in types).
        """
        if not self.has_active_consent():
            print(f"No active consent for {self} ({self.pk}). Skipping processing.")
            return False, "No consent found."
        # Optionally, call a hook for subclass-specific processing
        return self._process_data(*args, **kwargs)

    def _process_data(self, *args, **kwargs):
        """Override this in subclasses for actual processing logic."""
        pass

    def get_data_types(self):
        """List the data type names available for this source.

        These are the valid ``data_type`` values for ``fetch_data`` and
        ``count_rows``. Must not raise for connection problems; return []
        instead.

        Returns:
            List of data type names (str).
        """
        raise NotImplementedError("Subclasses must implement this method.")

    def fetch_data(self, data_type="battery", timestamp=0, limit=1000):
        """Fetch data from the source using a timestamp cursor.

        Sources that return True from ``supports_deletion()`` MUST honor
        ascending order and the soft-limit rule, because the download watermark
        relies on ``max(timestamp)`` being a contiguous high-water mark.

        No access check is made: the caller decides who may read the data
        (consent, ownership). Implementations may still refuse when there
        is no active consent (see ``JsonUrlDataSource``).

        Known deviations: ``JsonUrlDataSource`` returns a tuple or an error
        dict instead of a list in some cases, and ``NiimportDataSource`` has
        a different signature (date range and offset, no cursor).

        Args:
            data_type: Name of the data table/type to fetch, one of
                ``get_data_types()``.
            timestamp: Unix time in milliseconds (int). Return only rows whose
                ``timestamp`` field is >= this value. Defaults to 0 (all rows).
            limit: Soft maximum number of rows to return. The implementation
                MUST return up to ``limit`` rows but always include ALL rows
                that share the last returned timestamp, so the caller can
                safely advance its cursor to ``last_timestamp + 1`` without
                splitting equal-timestamp groups. This may cause the returned
                list to exceed ``limit``.

        Returns:
            A list of dicts, one per row, in **ascending** ``timestamp``
            order. Every row has ``timestamp`` (int, Unix ms); the other keys
            depend on the source and data type (see each subclass). An
            unknown data type or no data gives [].
        """
        raise NotImplementedError("Subclasses must implement this method.")

    def count_rows(self, data_type="battery", start_date=None, end_date=None):
        """Count the rows available for the given data_type and filters.

        Subclasses should override to provide an efficient count operation.

        Args:
            data_type: Data type name, one of ``get_data_types()``.
            start_date: Optional lower bound (datetime, or date where the
                source accepts it); None means unbounded.
            end_date: Optional upper bound, like ``start_date``.

        Returns:
            The number of rows.
        """
        raise NotImplementedError("Subclasses must implement this method.")

    def __str__(self):
        return f"{self.name} ({self.profile.user.username})"
