"""Rename StudySourceConfiguration.source_type values from class names to SOURCE_TYPE slugs."""
from django.db import migrations


# Map old class-name values to the new SOURCE_TYPE slug values.
RENAMES = {
    'GooglePortabilityDataSource': 'niimport',
    'TikTokPortabilityDataSource': 'niimport',
    'AwareDataSource': 'aware',
    'JsonUrlDataSource': 'json_url',
    'SurveyDataSource': 'survey',
}


def forwards(apps, schema_editor):
    StudySourceConfiguration = apps.get_model('studies', 'StudySourceConfiguration')

    # For niimport sources, preserve the sub-type in configuration
    for entry in StudySourceConfiguration.objects.filter(source_type='GooglePortabilityDataSource'):
        config = entry.configuration or {}
        config['niimport_source_type'] = 'google_portability'
        entry.configuration = config
        entry.source_type = 'niimport'
        entry.save()

    for entry in StudySourceConfiguration.objects.filter(source_type='TikTokPortabilityDataSource'):
        config = entry.configuration or {}
        config['niimport_source_type'] = 'tiktok_portability'
        entry.configuration = config
        entry.source_type = 'niimport'
        entry.save()

    # Simple renames for the rest
    StudySourceConfiguration.objects.filter(source_type='AwareDataSource').update(source_type='aware')
    StudySourceConfiguration.objects.filter(source_type='JsonUrlDataSource').update(source_type='json_url')
    StudySourceConfiguration.objects.filter(source_type='SurveyDataSource').update(source_type='survey')


def backwards(apps, schema_editor):
    StudySourceConfiguration = apps.get_model('studies', 'StudySourceConfiguration')
    for old_name, new_name in RENAMES.items():
        StudySourceConfiguration.objects.filter(source_type=new_name).update(source_type=old_name)


class Migration(migrations.Migration):

    dependencies = [
        ('studies', '0031_study_study_page_html'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='studysourceconfiguration',
            unique_together=set(),
        ),
        migrations.RunPython(forwards, backwards),
    ]
