from django.apps import AppConfig


class DataSourcesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'data_sources'

    def ready(self):
        # Importing forms registers the per-source-type setup and config forms
        # (DataSource.register_form_class / register_config_form_class).
        from . import forms  # noqa: F401
