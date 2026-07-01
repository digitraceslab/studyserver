from django.db import migrations


def copy_legal_text(apps, schema_editor):
    """Copy each Study's privacy notice / terms of service text into the new
    versioned models. Empty text is skipped so no blank version is created."""
    Study = apps.get_model("studies", "Study")
    PrivacyNotice = apps.get_model("studies", "PrivacyNotice")
    TermsOfService = apps.get_model("studies", "TermsOfService")

    for study in Study.objects.all():
        if study.privacy_notice_html:
            PrivacyNotice.objects.create(text=study.privacy_notice_html)
        if study.terms_of_service_html:
            TermsOfService.objects.create(text=study.terms_of_service_html)


class Migration(migrations.Migration):

    dependencies = [
        ("studies", "0039_privacynotice_termsofservice"),
    ]

    operations = [
        migrations.RunPython(copy_legal_text, migrations.RunPython.noop),
    ]
