"""Move QuestionCondition rows into the survey library's own table (fork
migration 0017) and drop the now-redundant extension model."""

from django.db import migrations


def copy_conditions(apps, schema_editor):
    OldCondition = apps.get_model("survey_extensions", "QuestionCondition")
    NewCondition = apps.get_model("survey", "QuestionCondition")
    for old in OldCondition.objects.all():
        NewCondition.objects.update_or_create(
            question_id=old.question_id,
            defaults={
                "depends_on_id": old.depends_on_id,
                "operator": old.operator,
                "choices": old.choices,
                "number": old.number,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("survey_extensions", "0002_remove_questionextra"),
        ("survey", "0017_questioncondition"),
    ]

    operations = [
        migrations.RunPython(copy_conditions, migrations.RunPython.noop),
        migrations.DeleteModel(name="QuestionCondition"),
    ]
