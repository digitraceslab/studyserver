"""Celery tasks of the data_sources app."""

from celery import shared_task
from .models import DataSource


@shared_task
def process_data_sources():
    """Call ``process()`` on every DataSource, in one pass.

    Scheduled by Celery beat (``CELERY_BEAT_SCHEDULE``, every 300 s).
    Sources without an active consent are skipped by ``process`` itself.
    The sources are base ``DataSource`` objects from the polymorphic
    manager. An exception in one source stops the pass for the rest.

    Returns:
        The string "Data sources processed."
    """
    data_sources = DataSource.objects.all()
    for source in data_sources:
        source.process()

    return "Data sources processed."
