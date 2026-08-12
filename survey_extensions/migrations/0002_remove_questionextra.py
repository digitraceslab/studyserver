"""Move the "other" option onto survey.Question (fork migration 0016) and drop
the now-redundant QuestionExtra table."""

from django.db import migrations


def copy_other_options(apps, schema_editor):
    QuestionExtra = apps.get_model("survey_extensions", "QuestionExtra")
    Question = apps.get_model("survey", "Question")
    for extra in QuestionExtra.objects.all():
        Question.objects.filter(pk=extra.question_id).update(
            other_option=extra.other_option, other_label=extra.other_label
        )


class Migration(migrations.Migration):
    dependencies = [
        ("survey_extensions", "0001_initial"),
        ("survey", "0016_question_other_option"),
    ]

    operations = [
        migrations.RunPython(copy_other_options, migrations.RunPython.noop),
        migrations.DeleteModel(name="QuestionExtra"),
    ]
