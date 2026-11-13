"""Forms of the data_sources app.

- Participant creation forms, one per built-in type, registered with
  ``@datasourceform`` and looked up by ``add_data_source``.
- Admin StudySourceConfiguration forms that store type-specific settings
  in the configuration JSON, registered with ``@datasourceconfigform``.
- ``DataFilterForm``, the data browser filter of ``view_data_source``.
"""

from django import forms

# StudySourceConfiguration forms for datasource-specific configuration
from studies.models import StudySourceConfiguration

from .models import AwareDataSource, JsonUrlDataSource, NiimportDataSource
from .models.base import DataSource
from .models.model_registration import datasourceconfigform, datasourceform


class BaseDataSourceForm(forms.ModelForm):
    """Base form for data source creation.

    Args:
        configuration: Optional keyword argument, the configuration dict
            passed by the add_data_source view; stored as
            ``self.configuration`` (empty dict when missing or empty).
    """

    def __init__(self, *args, **kwargs):
        self.configuration = kwargs.pop("configuration", {}) or {}
        super().__init__(*args, **kwargs)


@datasourceform(JsonUrlDataSource)
class JsonUrlDataSourceForm(BaseDataSourceForm):
    """Create or edit a JSON URL source: name and URL."""

    class Meta:
        model = JsonUrlDataSource
        fields = ["name", "url"]


@datasourceform(AwareDataSource)
class AwareDataSourceForm(BaseDataSourceForm):
    """Create or edit an AWARE source: name only, so ``add_data_source``
    creates it without showing the form when completing an open consent."""

    class Meta:
        model = AwareDataSource
        fields = ["name"]


@datasourceform(NiimportDataSource)
class NiimportDataSourceForm(BaseDataSourceForm):
    """Create a Niimport source: name and variant.

    When ``configuration`` names a ``niimport_source_type``, the variant
    field is preset and disabled, and ``save`` fills it from the
    configuration if empty.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        niimport_source_type = self.configuration.get("niimport_source_type")
        if niimport_source_type:
            self.fields["niimport_source_type"].initial = niimport_source_type
            self.fields["niimport_source_type"].disabled = True

    class Meta:
        model = NiimportDataSource
        fields = ["name", "niimport_source_type"]

    def save(self, commit=True):
        """Save the source, filling an empty variant from the configuration.

        Args:
            commit: Whether to write the instance to the database.

        Returns:
            The Niimport source instance.
        """
        instance = super().save(commit=False)
        if not instance.niimport_source_type:
            instance.niimport_source_type = self.configuration.get(
                "niimport_source_type", ""
            )
        if commit:
            instance.save()
        return instance


@datasourceconfigform(NiimportDataSource)
class NiimportStudySourceConfigurationForm(forms.ModelForm):
    """Form for StudySourceConfiguration when source_type is NiimportDataSource.

    Adds the required ``niimport_source_type`` and ``niimport_server_url``
    fields, stored in ``configuration`` alongside its other keys.
    """

    niimport_source_type = forms.ChoiceField(
        choices=NiimportDataSource.NIIMPORT_SOURCE_TYPE_CHOICES,
        required=True,
        label="Portability source type",
    )
    niimport_server_url = forms.URLField(
        required=True,
        label="Niimport server URL",
        help_text="The URL of the Niimport server",
    )

    class Meta:
        model = StudySourceConfiguration
        fields = [
            "study",
            "source_type",
            "status",
            "show_separate_consent_page",
            "requested_data_types",
            "configuration",
            "consent_template_html",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-populate niimport_source_type from configuration if present
        if self.instance and self.instance.configuration:
            self.fields[
                "niimport_source_type"
            ].initial = self.instance.configuration.get("niimport_source_type")
            self.fields[
                "niimport_server_url"
            ].initial = self.instance.configuration.get("niimport_server_url")

    def save(self, commit=True):
        """Save, storing both added fields in ``configuration``.

        Args:
            commit: Whether to write the instance to the database.

        Returns:
            The StudySourceConfiguration instance.
        """
        instance = super().save(commit=False)
        # Store niimport_source_type in configuration JSON, preserving any other keys.
        instance.configuration = {
            **(instance.configuration or {}),
            "niimport_source_type": self.cleaned_data.get("niimport_source_type"),
            "niimport_server_url": self.cleaned_data.get("niimport_server_url"),
        }
        if commit:
            instance.save()
        return instance


@datasourceconfigform(AwareDataSource)
class AwareStudySourceConfigurationForm(forms.ModelForm):
    """Form for StudySourceConfiguration when source_type is AwareDataSource.

    Adds the optional ``aware_config_version`` field, stored in
    ``configuration`` alongside its other keys.
    """

    aware_config_version = forms.CharField(
        required=False,
        label="AWARE config version",
        help_text="The config file version on the aware server (config_v<version>.json)",
    )

    class Meta:
        model = StudySourceConfiguration
        fields = [
            "study",
            "source_type",
            "status",
            "show_separate_consent_page",
            "requested_data_types",
            "consent_template_html",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-populate the version from configuration; a missing key is "".
        configuration = (self.instance and self.instance.configuration) or {}
        self.fields["aware_config_version"].initial = configuration.get(
            "aware_config_version", ""
        )

    def save(self, commit=True):
        """Save, storing the version in ``configuration``.

        Args:
            commit: Whether to write the instance to the database.

        Returns:
            The StudySourceConfiguration instance.
        """
        instance = super().save(commit=False)
        # Store the version in configuration JSON, preserving any other keys.
        instance.configuration = {
            **(instance.configuration or {}),
            "aware_config_version": self.cleaned_data.get("aware_config_version", ""),
        }
        if commit:
            instance.save()
        return instance


class DataFilterForm(forms.Form):
    """Pick a data type and optional date range for browsing a source.

    Args:
        data_type_choices: Keyword argument, list of data type names that
            fills the required data type choices (shown title-cased).
    """

    data_type = forms.ChoiceField(choices=[])
    start_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    end_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, *args, **kwargs):
        data_type_choices = kwargs.pop("data_type_choices", [])
        super().__init__(*args, **kwargs)
        self.fields["data_type"].choices = [
            (dt, dt.title()) for dt in data_type_choices
        ]
