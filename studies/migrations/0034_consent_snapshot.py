from django.db import migrations, models


def forwards(apps, schema_editor):
    Consent = apps.get_model('studies', 'Consent')
    for consent in Consent.objects.select_related('source_configuration').all():
        cfg = consent.source_configuration
        if cfg:
            update_fields = []
            consent.configuration = dict(cfg.configuration or {})
            update_fields.append('configuration')
            consent.requested_data_types = cfg.requested_data_types or ''
            update_fields.append('requested_data_types')
            consent.data_end = cfg.data_end
            update_fields.append('data_end')
            if consent.data_start is None:
                consent.data_start = cfg.data_start
                update_fields.append('data_start')
            if update_fields:
                consent.save(update_fields=update_fields)


def backwards(apps, schema_editor):
    Consent = apps.get_model('studies', 'Consent')
    Consent.objects.update(data_end=None, requested_data_types='', configuration={})


class Migration(migrations.Migration):

    dependencies = [
        ('studies', '0033_consent_source_configuration'),
    ]

    operations = [
        migrations.AddField(
            model_name='consent',
            name='data_end',
            field=models.DateTimeField(blank=True, help_text='End of the data collection period. May be null for ongoing collection.', null=True),
        ),
        migrations.AddField(
            model_name='consent',
            name='requested_data_types',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='consent',
            name='configuration',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(forwards, backwards),
    ]
