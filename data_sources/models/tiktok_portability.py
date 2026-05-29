"""TikTok Portability data source — proxies to portability-server."""
import logging

from django.conf import settings
from django.db import models

from studies.models import StudySourceConfiguration
from .base import DataSource
from . import portability_client

logger = logging.getLogger(__name__)


class TikTokPortabilityDataSource(DataSource):
    PROCESSING_STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('authorized', 'Authorized, waiting for data'),
        ('processing', 'Processing'),
        ('processed', 'Processed successfully'),
        ('error', 'Error during processing'),
    )

    donation_id = models.IntegerField(null=True, blank=True)
    donation_token = models.UUIDField(null=True, blank=True)
    processing_status = models.CharField(
        max_length=20,
        choices=PROCESSING_STATUS_CHOICES,
        default='pending',
    )
    processing_log = models.TextField(blank=True, default='')

    requires_setup = True
    requires_confirmation = False

    @property
    def display_type(self):
        return "TikTok Portability Data"

    PORTABILITY_SOURCE_TYPE = 'tiktok_portability'

    def _get_study_config(self):
        """Look up study configuration for this source via its linked consent."""
        model_name = type(self).__name__
        consent = self.consents.select_related('study').first()
        if not consent:
            return {}
        study = consent.study
        kwargs = {}
        data_start, data_end = study.get_source_dates(model_name)
        if data_start:
            kwargs['data_start_date'] = data_start.date()
        if data_end:
            kwargs['data_end_date'] = data_end.date()
        source_config = StudySourceConfiguration.objects.filter(
            study=study,
            source_type=model_name
        ).first()
        if source_config and source_config.requested_data_types:
            kwargs['requested_data_types'] = source_config.requested_data_types
        return kwargs

    def _create_donation(self):
        """Create a donation on the portability server and store the result."""
        kwargs = self._get_study_config()
        donation = portability_client.create_donation(
            self.PORTABILITY_SOURCE_TYPE, **kwargs
        )
        self.donation_id = donation['id']
        self.donation_token = donation['token']
        self.save()

    def get_setup_url(self):
        if not self.donation_id:
            self._create_donation()
        if self.donation_token:
            return f"{settings.PORTABILITY_SERVER_URL}/donate/{self.donation_token}/"
        return None

    def get_data_types(self):
        if not self.donation_id:
            return []
        try:
            result = portability_client.get_data(self.donation_id)
            return result.get('data_types', [])
        except Exception as e:
            logger.warning("Failed to get data types from portability server: %s", e)
            return []

    def fetch_data(self, data_type, limit=1000, start_date=None, end_date=None, offset=0):
        if not self.donation_id:
            return []
        try:
            result = portability_client.get_data(
                self.donation_id,
                data_type=data_type,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
                offset=offset,
            )
            return result.get('data', [])
        except Exception as e:
            logger.warning("Failed to fetch data from portability server: %s", e)
            return []

    def count_rows(self, data_type, start_date=None, end_date=None):
        if not self.donation_id:
            return 0
        try:
            result = portability_client.get_data(
                self.donation_id,
                data_type=data_type,
                start_date=start_date,
                end_date=end_date,
                limit=0,
            )
            return result.get('count', 0)
        except Exception as e:
            logger.warning("Failed to count rows from portability server: %s", e)
            return 0

    def revoke_before_delete(self):
        if self.donation_id:
            try:
                portability_client.delete_donation(self.donation_id)
            except Exception as e:
                logger.warning("Failed to delete donation on portability server: %s", e)

    def _process_data(self):
        """Poll portability-server for status updates."""
        if not self.donation_id:
            return
        try:
            donation = portability_client.get_donation(self.donation_id)
            remote_status = donation.get('status', '')
            if remote_status == 'processed':
                self.processing_status = 'processed'
                self.status = 'active'
            elif remote_status == 'error':
                self.processing_status = 'error'
            elif remote_status in ('authorized', 'processing'):
                self.processing_status = remote_status
                self.status = 'processing'
            self.save()
        except Exception as e:
            logger.warning("Failed to poll portability server: %s", e)
