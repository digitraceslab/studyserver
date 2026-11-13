from django.apps import AppConfig


class SurveyDatasourceConfig(AppConfig):
    """App configuration of survey_datasource.

    Imports the forms module on startup so the creation form gets registered.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'survey_datasource'

    def ready(self):
        """Import the forms module so ``SurveyDataSourceForm`` is registered."""
        from . import forms
