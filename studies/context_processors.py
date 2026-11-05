"""Study template context.

Makes the configured study available to every template rendered with a
request, so base.html and other shared templates can use it without each
view passing it in. Values are lazy: the database is only queried when a
template actually uses them. Context passed by a view overrides these.
"""

from django.utils.functional import SimpleLazyObject


def study_context(request):
    """Add the study and its assets dictionary to the template context."""
    from studies.models import Study

    study = SimpleLazyObject(lambda: Study.objects.first())

    def get_assets():
        return study.get_assets_dict() if study else {}

    return {
        "study": study,
        "assets": SimpleLazyObject(get_assets),
    }
