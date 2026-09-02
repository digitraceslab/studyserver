from django.apps import AppConfig
from django.db.models.signals import post_migrate


def setup_researcher_group(sender, **kwargs):
    from django.contrib.auth.models import Group, Permission

    group, created = Group.objects.get_or_create(name="Researchers")

    required_permissions = [
        "view_study",
        "change_study",
        "add_studyasset",
        "change_studyasset",
        "view_studyasset",
        "delete_studyasset",
        "view_studyparticipant",
        "view_consent",
        "view_studysourceconfiguration",
        "change_studysourceconfiguration",
        "delete_studysourceconfiguration",
        "add_studysourceconfiguration",
        "add_survey",
        "change_survey",
        "view_survey",
        "add_question",
        "change_question",
        "view_question",
        "delete_question",
        "add_category",
        "change_category",
        "view_category",
        "add_privacynotice",
        "change_privacynotice",
        "view_privacynotice",
        "delete_privacynotice",
        "add_termsofservice",
        "change_termsofservice",
        "view_termsofservice",
        "delete_termsofservice",
        "add_questioncondition",
        "change_questioncondition",
        "view_questioncondition",
        "delete_questioncondition",
        "add_questionextra",
        "change_questionextra",
        "view_questionextra",
        "add_csssnippet",
        "change_csssnippet",
        "view_csssnippet",
        "delete_csssnippet",
    ]

    perms = Permission.objects.filter(codename__in=required_permissions)
    group.permissions.set(perms)


class StudiesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "studies"
    verbose_name = "Study"

    def ready(self):
        # Connect the function to the post_migrate signal
        post_migrate.connect(setup_researcher_group, sender=self)
