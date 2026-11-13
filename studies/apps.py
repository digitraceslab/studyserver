"""Studies app config and the Researchers permission group.

After each migrate, the "Researchers" group is created if missing and its
permissions are reset to the list in ``_setup_researcher_group``.
"""

from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _setup_researcher_group(sender, **kwargs):
    """post_migrate handler: set the "Researchers" group's admin permissions.

    The list is hand-written; a model researchers edit in the admin needs its
    codenames added here. Permissions added to the group by hand are removed.
    """
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
        "add_infosheet",
        "change_infosheet",
        "view_infosheet",
        "delete_infosheet",
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
    """Connects ``_setup_researcher_group`` to post_migrate."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "studies"
    verbose_name = "Study"

    def ready(self):
        # Connect the function to the post_migrate signal
        post_migrate.connect(_setup_researcher_group, sender=self)
