"""Niimport data source: data donated through the portability server.

One concrete type (SOURCE_TYPE "niimport") covers several variants, chosen
by ``niimport_source_type`` (Google / TikTok portability, TikTok export).
"""

import logging

from django.conf import settings
from django.db import models

from .base import DataSource
from ._portability_client import PortabilityClient

logger = logging.getLogger(__name__)

# Shared researcher API client; holds the portability-server session token.
_portability_client_api = PortabilityClient()


class NiimportDataSource(DataSource):
    """Data source that proxies to a donation on the Niimport server.

    Setup creates a donation and redirects the participant to its donate
    page; the periodic ``process`` polls the donation and makes the source
    "active" once it is processed. Data types and rows come from the
    server. Does not support deletion.
    """

    SOURCE_TYPE = "niimport"

    PROCESSING_STATUS_CHOICES = (
        ("pending", "Pending"),
        ("authorized", "Authorized, waiting for data"),
        ("processing", "Processing"),
        ("processed", "Processed successfully"),
        ("error", "Error during processing"),
    )

    NIIMPORT_SOURCE_TYPE_CHOICES = (
        ("google_portability", "Google Portability"),
        ("tiktok_portability", "TikTok Portability"),
        ("tiktok_export", "TikTok Export"),
    )

    niimport_source_type = models.CharField(
        max_length=30,
        choices=NIIMPORT_SOURCE_TYPE_CHOICES,
    )
    donation_id = models.IntegerField(null=True, blank=True)
    donation_token = models.UUIDField(null=True, blank=True)
    processing_status = models.CharField(
        max_length=20,
        choices=PROCESSING_STATUS_CHOICES,
        default="pending",
    )
    processing_log = models.TextField(blank=True, default="")

    requires_setup = True
    requires_confirmation = False

    # Human-readable label per portability variant. Single source of truth for
    # both the instance display (keyed off the db column) and the config-space
    # resolver (keyed off StudySourceConfiguration.configuration).
    DISPLAY_NAMES = {
        "google_portability": "Google Portability Data",
        "tiktok_portability": "TikTok Portability Data",
        "tiktok_export": "TikTok Export Data",
    }

    @property
    def _NIIMPORT_SOURCE_TYPE(self):
        """The variant slug, same as ``niimport_source_type``."""
        return self.niimport_source_type

    @classmethod
    def display_type_for_configuration(cls, configuration):
        """Return the display name of a configuration's variant.

        Args:
            configuration: Configuration dict (or None) whose
                ``niimport_source_type`` names the variant.

        Returns:
            The variant's display name, or "Missing portability data type".
        """
        # Config-space resolver: a StudySourceConfiguration has no db column, so the
        # variant is read from its configuration JSON.
        variant = (configuration or {}).get("niimport_source_type")
        return cls.DISPLAY_NAMES.get(variant, "Missing portability data type")

    @property
    def display_type(self):
        """Display name of this source's ``niimport_source_type`` variant."""
        # For a live source the niimport_source_type db column is authoritative;
        # the variant is never read from self.configuration.
        return self.DISPLAY_NAMES.get(
            self.niimport_source_type, "Missing portability data type"
        )

    def _get_study_config(self):
        """Build donation kwargs from values stored on this datasource."""
        kwargs = {}
        data_start, data_end = self.data_start, self.data_end
        if data_start:
            kwargs["data_start_date"] = data_start
        if data_end:
            kwargs["data_end_date"] = data_end
        requested_data_types = (self.configuration or {}).get("requested_data_types")
        if requested_data_types is not None:
            if isinstance(requested_data_types, str):
                requested_data_types = [
                    item.strip()
                    for item in requested_data_types.split(",")
                    if item.strip()
                ]
            kwargs["requested_data_types"] = requested_data_types
        return kwargs

    def _create_donation(self):
        """Create a donation on the Niimport server and store the result."""
        kwargs = self._get_study_config()
        donation = _portability_client_api.create_donation(
            self._NIIMPORT_SOURCE_TYPE, **kwargs
        )
        self.donation_id = donation["id"]
        self.donation_token = donation["token"]
        self.save()

    def get_setup_url(self):
        """Return the donate page URL on the portability server.

        Creates the donation first (and saves the source) when there is
        none yet, passing ``data_start`` / ``data_end`` and the configured
        ``requested_data_types``.

        Returns:
            The donate page URL, or None when the server gave no token.

        Raises:
            requests.HTTPError: When the server request fails.
        """
        if not self.donation_id:
            self._create_donation()
        if self.donation_token:
            return f"{settings.PORTABILITY_SERVER_URL}/donate/{self.donation_token}/"
        return None

    def get_data_types(self):
        """Return the donation's data types from the server.

        Returns:
            The data types; [] without a donation or when the request
            fails (logged).
        """
        if not self.donation_id:
            return []
        try:
            result = _portability_client_api.get_data(self.donation_id)
            return result.get("data_types", [])
        except Exception as e:
            logger.warning("Failed to get data types from Niimport server: %s", e)
            return []

    def fetch_data(self, data_type, timestamp=0, limit=1000):
        """Return donated rows of ``data_type`` from the server.

        Has the ``DataSource.fetch_data`` signature but does not behave
        like it yet: ``timestamp`` is ignored, so it always returns the
        first page of up to ``limit`` rows, with no equal-timestamp
        extension. Row fields and ordering are whatever the server returns.

        Args:
            data_type: The data type to fetch.
            timestamp: Ignored (see above).
            limit: Maximum number of rows.

        Returns:
            The rows; [] without a donation or when the request fails
            (logged).
        """
        # TODO: honor the timestamp cursor once the niimport API supports it.
        if not self.donation_id:
            return []
        try:
            result = _portability_client_api.get_data(
                self.donation_id,
                data_type=data_type,
                limit=limit,
            )
            return result.get("data", [])
        except Exception as e:
            logger.warning("Failed to fetch data from Niimport server: %s", e)
            return []

    def count_rows(self, data_type, start_date=None, end_date=None):
        """Return the server's row count for ``data_type`` in the date range.

        Args:
            data_type: The data type to count.
            start_date: Optional start of the date range.
            end_date: Optional end of the date range.

        Returns:
            The row count; 0 without a donation or when the request fails
            (logged).
        """
        if not self.donation_id:
            return 0
        try:
            result = _portability_client_api.get_data(
                self.donation_id,
                data_type=data_type,
                start_date=start_date,
                end_date=end_date,
                limit=0,
            )
            return result.get("count", 0)
        except Exception as e:
            logger.warning("Failed to count rows from Niimport server: %s", e)
            return 0

    def revoke_before_delete(self):
        """Delete the donation on the server; failures are only logged."""
        if self.donation_id:
            try:
                _portability_client_api.delete_donation(self.donation_id)
            except Exception as e:
                logger.warning("Failed to delete donation on Niimport server: %s", e)

    def _process_data(self):
        """Poll Niimport server for status updates."""
        if not self.donation_id:
            return
        try:
            donation = _portability_client_api.get_donation(self.donation_id)
            remote_status = donation.get("status", "")
            if remote_status == "processed":
                self.processing_status = "processed"
                self.status = "active"
            elif remote_status == "error":
                self.processing_status = "error"
            elif remote_status in ("authorized", "processing"):
                self.processing_status = remote_status
                self.status = "processing"
            self.save()
        except Exception as e:
            logger.warning("Failed to poll Niimport server: %s", e)
