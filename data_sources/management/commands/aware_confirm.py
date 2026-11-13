"""Management command aware_confirm: confirm pending AWARE sources."""

from django.core.management.base import BaseCommand
from data_sources.models import AwareDataSource
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Confirm every AWARE data source with status "pending".

    Calls ``confirm_device()`` on each, logs success or failure per source
    and prints how many were attempted.
    """

    help = 'Confirm all AWARE data sources for the specified user profile.'

    def handle(self, *args, **options):
        """Run the confirmation pass; takes no arguments."""
        pending_sources = AwareDataSource.objects.filter(status='pending')
        for source in pending_sources:
            success, message = source.confirm_device()
            if success:
                logger.info(f"Successfully confirmed AWARE device for source ID {source.id}: {message}")
            else:
                logger.warning(f"Failed to confirm AWARE device for source ID {source.id}: {message}")
        
        self.stdout.write(f"Attempted to confirm {pending_sources.count()} AWARE data sources.")