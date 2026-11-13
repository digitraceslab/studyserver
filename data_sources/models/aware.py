"""AWARE data source: a participant's phone running the AWARE app.

The phone uploads into the AWARE MySQL database (``_db_connector``). The
source is matched to its rows by ``device_label``, which the participant
enters in the app through the setup page reached from the QR code.
"""

import base64
import io
import uuid

import qrcode
import requests
from django.conf import settings
from django.contrib import messages
from django.db import models
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from studies.models import Consent

from . import _db_connector
from .base import DataSource


class AwareDataSource(DataSource):
    """Data source backed by the AWARE mobile app database.

    Needs setup (instructions page with a QR code) and confirmation (the
    device must have uploaded data). Data types are the device's AWARE
    tables; rows are the columns of ``<table>_transformed`` plus
    ``device_id``. Supports deletion.
    """

    SOURCE_TYPE = "aware"

    device_label = models.CharField(max_length=150, unique=True, default=uuid.uuid4)

    requires_setup = True
    requires_confirmation = True

    def get_setup_url(self):
        """Return the URL of this source's ``instructions`` view."""
        base_url = reverse("instructions", args=[self.id])
        return base_url

    def get_confirm_url(self):
        """Return the URL of this source's ``confirm_data_source`` view."""
        base_url = reverse("confirm_data_source", args=[self.id])
        return base_url

    @property
    def display_type(self):
        """Always "AWARE Mobile Data"."""
        return self.display_type_for_configuration(self.configuration)

    @classmethod
    def display_type_for_configuration(cls, configuration):
        """Always "AWARE Mobile Data"; the configuration is ignored."""
        return "AWARE Mobile Data"

    def get_instructions_card(self, request, consent_id=None, study_id=None):
        """Return the context and template of the AWARE setup card.

        The context holds a QR code (base64 PNG) and link to the
        ``datasource_token_view`` "setup" page, which contains this source's
        secret ``config_token``.

        Args:
            request: The current HTTP request, used to build the absolute URL.
            consent_id: Id of the consent, passed on in the context.
            study_id: Unused.

        Returns:
            A ``(context, template_name)`` tuple.
        """
        mobile_setup_url = request.build_absolute_uri(
            reverse(
                "datasource_token_view",
                kwargs={"token": self.config_token, "view_type": "setup"},
            )
        )
        qr_img = qrcode.make(mobile_setup_url)
        buffer = io.BytesIO()
        qr_img.save(buffer, format="PNG")
        qr_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        context = {
            "source": self,
            "consent_id": consent_id,
            "qr_code_image": qr_b64,
            "qr_link": mobile_setup_url,
        }
        return context, "data_sources/aware/instructions_card.html"

    def _get_config_url(self):
        """Link to this participant's study config on the aware server at AWARE_DB_HOST.

        The version comes from the first active consent's snapshotted configuration;
        a missing or empty version is 0.
        """
        consent = Consent.objects.filter(
            participant=self.profile,
            data_source_id=self.id,
            is_complete=True,
            revocation_date__isnull=True,
        ).first()
        configuration = (consent.configuration if consent else None) or {}
        version = configuration.get("aware_config_version") or "0"
        return f"https://{settings.AWARE_DB_HOST}/v{version}/{self.device_label}/"

    def show_links(self):
        """Keep the setup instructions reachable from the dashboard after setup."""
        return [(self.get_setup_url(), "Setup instructions")]

    def _check_for_device(self):
        """Link the device that uploaded data under ``device_label``.

        Already active: success, no change. Otherwise looks up device ids for
        the label in the AWARE database; none found, or the device already
        claimed by another user's AWARE source, is a failure. On success
        stores the first device id, sets ``status`` to "active" and saves.

        Returns:
            A ``(success, message)`` tuple.
        """
        if self.status == "active":
            return (True, "This device is already active.")

        retrieved_device_ids = _db_connector.get_device_ids_for_label(self.device_label)

        if not retrieved_device_ids:
            return (
                False,
                "No data with that device label. It may take a few hours for data to appear. Please ensure AWARE is running on your device.",
            )

        is_claimed = (
            AwareDataSource.objects.filter(device_id__in=retrieved_device_ids)
            .exclude(id=self.id)
            .exclude(profile=self.profile)
            .exists()
        )
        if is_claimed:
            return (
                False,
                "Error: This device ID has already been claimed by another user. Contact the administrator if you believe this is an error.",
            )

        self.device_id = retrieved_device_ids[0]
        self.status = "active"
        self.save()
        return (True, "AWARE device confirmed and linked successfully!")

    def _process_data(self):
        """Try to link the device (see ``_check_for_device``), printing any error."""
        result, message = self._check_for_device()
        if not result:
            print(f"Data processing error for {self}: {message}")

    def confirm(self, request):
        """Link the uploading device by running ``_check_for_device``.

        Returns:
            The ``(success, message)`` tuple of ``_check_for_device``.
        """
        result, message = self._check_for_device()
        return result, message

    def handle_token_view(self, request, token, view_type):
        """Serve the token-authenticated pages used by the phone.

        Called by ``views.token_view_dispatcher``; holding ``config_token`` is
        the only authorization, no login is needed. Any HTTP method.

        Args:
            request: The current HTTP request.
            token: The ``config_token`` from the URL.
            view_type: Which page to serve:

                - "setup": HTML page with the AWARE config URL and device
                  label.
                - "config": JSON AWARE study config built from this source's
                  active consents (study info of the first, merged questions /
                  schedules / sensors of all). It includes the AWARE database
                  insert credentials. A POST body is only printed.
                - "client_get_study_info": JSON study info of the first
                  active consent, or 404 JSON ``{"error": ...}`` when there
                  is none.

        Returns:
            The HTTP response. Returns None for any other ``view_type`` (the
            view then raises, giving a 500). When ``token`` does not match it
            returns a tuple, not a response (also a 500); this cannot happen
            through the dispatcher, which looks the source up by the token.
        """
        if str(self.config_token) != str(token):
            return (False, "Invalid configuration token.")

        if view_type == "setup":
            config_url = self._get_config_url()

            context = {
                "source": self,
                "config_url": config_url,
                "device_label": self.device_label,
            }
            return render(request, "data_sources/aware/mobile_setup.html", context)

        elif view_type == "config":
            if request.method == "POST":
                # for now just print what was posted
                print("Received AWARE config POST:", request.POST)
            active_consents = Consent.objects.filter(
                participant=self.profile,
                data_source_id=self.id,
                is_complete=True,
                revocation_date__isnull=True,
            )
            studies = [consent.study for consent in active_consents]
            study = studies[0] if studies else None
            name_parts = (
                study.contact_name.rsplit(" ", 1)
                if study and study.contact_name
                else ["", ""]
            )
            researcher_first = name_parts[0]
            researcher_last = name_parts[1] if len(name_parts) > 1 else ""
            config_json = {
                "_id": study.title if study else "",
                "study_info": {
                    "study_title": study.title if study else "",
                    "study_description": study.description if study else "",
                    "researcher_first": researcher_first,
                    "researcher_last": researcher_last,
                    "researcher_contact": study.contact_email if study else "",
                },
                "database": {
                    "rootPassword": "-",
                    "rootUsername": "-",
                    "database_host": settings.AWARE_DB_HOST,
                    "database_port": settings.AWARE_DB_PORT,
                    "database_name": settings.AWARE_DB_NAME,
                    "database_password": settings.AWARE_DB_INSERT_PASSWORD,
                    "database_username": settings.AWARE_DB_INSERT_USER,
                    "require_ssl": True,
                    "config_without_password": False,
                },
                "createdAt": "",
                "updatedAt": "2025-09-25T12:30:13.411Z",
                "questions": [],
                "schedules": [],
                "sensors": [
                    {"setting": "device_label", "value": self.device_label},
                ],
            }
            # Merge each consent's own snapshotted configuration (the authoritative
            # record of what the participant agreed to).
            # Orphan/personal/testing sources have no consent and no configuration.
            for consent in active_consents:
                study_config = consent.configuration or {}
                config_json["questions"].extend(study_config.get("questions", []))
                config_json["schedules"].extend(study_config.get("schedules", []))
                config_json["sensors"].extend(study_config.get("sensors", []))
            # Deduplicate sensors by "setting" key, keeping the last occurrence
            seen = {}
            for sensor in config_json["sensors"]:
                seen[sensor.get("setting")] = sensor
            config_json["sensors"] = list(seen.values())
            return JsonResponse(config_json)

        elif view_type == "client_get_study_info":
            # Find the active consent that links to this data source (reverse FK).
            consent = self.consents.filter(
                is_complete=True, revocation_date__isnull=True
            ).first()
            if not consent:
                return JsonResponse({"error": "No active consent found."}, status=404)

            study = consent.study
            name_parts = (
                study.contact_name.rsplit(" ", 1) if study.contact_name else ["", ""]
            )
            researcher_first = name_parts[0]
            researcher_last = name_parts[1] if len(name_parts) > 1 else ""
            study_info = {
                "study_title": study.title,
                "study_description": study.description,
                "researcher_first": researcher_first,
                "researcher_last": researcher_last,
                "researcher_contact": study.contact_email,
            }
            return JsonResponse(study_info)

    def get_data_types(self):
        """List the AWARE tables that hold data for this device.

        Returns:
            Table names; [] until the source is active with a device id.
        """
        print("Getting AWARE data types...", self.device_label)
        if self.status == "active" and self.device_id:
            tables = _db_connector.get_aware_tables(self.device_label)
            return tables if tables else []
        return []

    def fetch_data(self, data_type="battery", timestamp=0, limit=1000):
        """Fetch rows of AWARE table ``data_type`` as ``DataSource.fetch_data`` describes.

        Rows are the columns of ``<data_type>_transformed`` plus
        ``device_id``. Does not check consent.

        Args:
            data_type: AWARE table name.
            timestamp: Unix-ms cursor; only rows at or after it are returned.
            limit: Soft maximum number of rows.

        Returns:
            The rows; [] until the source is active with a device id.
        """
        print("Getting AWARE data...", self.device_label)
        if self.status == "active" and self.device_id:
            return _db_connector.get_aware_data(
                self.device_label, data_type, timestamp=timestamp, limit=limit
            )
        return []

    def count_rows(self, data_type="battery", start_date=None, end_date=None):
        """Count the rows available for the given AWARE data_type.

        Args:
            data_type: AWARE table name.
            start_date: Optional lower bound.
            end_date: Optional upper bound.

        Returns:
            The row count; 0 until the source is active with a device id.
        """
        if self.status == "active" and self.device_id:
            return _db_connector.get_aware_count(
                self.device_label, data_type, start_date, end_date
            )
        return 0

    def supports_deletion(self):
        """Always True."""
        return True

    def latest_timestamp(self, data_type, start_date=None, end_date=None):
        """Find the newest timestamp in the AWARE table.

        Args:
            data_type: AWARE table name.
            start_date: Optional lower bound of the range considered.
            end_date: Optional upper bound of the range considered.

        Returns:
            The newest ``timestamp`` (Unix ms), or None when the source is
            not active or there is no data.
        """
        if self.status == "active" and self.device_id:
            return _db_connector.get_aware_max_timestamp(
                self.device_label, data_type, start_date, end_date
            )
        return None

    def mark_deletable(self, data_type, through):
        """Queue an AWARE deletion request for all rows with timestamp <= through.

        Args:
            data_type: AWARE table name.
            through: Inclusive Unix-ms timestamp up to which rows are deleted.
        """
        # through is inclusive → daemon deletes timestamp < delete_before
        delete_before = int(through) + 1
        return _db_connector.insert_deletion_request(
            self.device_label, data_type, delete_before
        )

    def unmark_deletable(self):
        """Remove all pending AWARE deletion requests for this device."""
        return _db_connector.remove_deletion_requests(self.device_label)
