"""Normalize Consent.source_type from legacy class names to SOURCE_TYPE slugs.

Migrations 0032/0033 normalized StudySourceConfiguration.source_type and linked each
Consent to a StudySourceConfiguration, but never rewrote Consent.source_type itself.
The current code treats Consent.source_type as a slug (display labels, export column),
so legacy values like 'AwareDataSource' / 'GooglePortabilityDataSource' must be migrated.
"""
from django.db import migrations


# Legacy class-name -> SOURCE_TYPE slug (mirrors 0033's LEGACY_CONSENT_SOURCE_MAP).
LEGACY_SOURCE_MAP = {
    'AwareDataSource': 'aware',
    'JsonUrlDataSource': 'json_url',
    'SurveyDataSource': 'survey',
    'NiimportDataSource': 'niimport',
    'GooglePortabilityDataSource': 'niimport',
    'TikTokPortabilityDataSource': 'niimport',
    'TikTokExportDataSource': 'niimport',
}


def forwards(apps, schema_editor):
    Consent = apps.get_model('studies', 'Consent')
    for consent in Consent.objects.select_related('source_configuration').all():
        if consent.source_configuration_id:
            # The linked config is already slug-normalized by 0032.
            new_value = consent.source_configuration.source_type
        else:
            new_value = LEGACY_SOURCE_MAP.get(consent.source_type)
        if new_value and new_value != consent.source_type:
            consent.source_type = new_value
            consent.save(update_fields=['source_type'])


def backwards(apps, schema_editor):
    # Irreversible normalization; nothing to undo.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('studies', '0034_consent_snapshot'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
