"""App configuration of the data_sources app."""

from django.apps import AppConfig


class DataSourcesConfig(AppConfig):
    """Django app config for data_sources."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'data_sources'

    def ready(self):
        """Import the forms module so the per-type forms get registered."""
        # Importing forms registers the per-source-type setup and config forms
        # (DataSource.register_form_class / register_config_form_class).
        from . import forms  # noqa: F401
