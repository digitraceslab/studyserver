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

from . import db_connector
from .base import DataSource


class AwareDataSource(DataSource):
    SOURCE_TYPE = "aware"

    device_label = models.CharField(max_length=150, unique=True, default=uuid.uuid4)

    requires_setup = True
    requires_confirmation = True

    def get_setup_url(self):
        base_url = reverse("instructions", args=[self.id])
        return base_url

    def get_confirm_url(self):
        base_url = reverse("confirm_data_source", args=[self.id])
        return base_url

    @property
    def display_type(self):
        return self.display_type_for_configuration(self.configuration)

    @classmethod
    def display_type_for_configuration(cls, configuration):
        return "AWARE Mobile Data"

    def get_instructions_card(self, request, consent_id=None, study_id=None):
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

    def check_for_device(self):
        if self.status == "active":
            return (True, "This device is already active.")

        retrieved_device_ids = db_connector.get_device_ids_for_label(self.device_label)

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
        result, message = self.check_for_device()
        if not result:
            print(f"Data processing error for {self}: {message}")

    def confirm(self, request):
        result, message = self.check_for_device()
        return result, message

    def handle_token_view(self, request, token, view_type):
        if str(self.config_token) != str(token):
            return (False, "Invalid configuration token.")

        if view_type == "setup":
            config_url = request.build_absolute_uri(
                reverse(
                    "datasource_token_view",
                    kwargs={"token": self.config_token, "view_type": "config"},
                )
            )

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
        """Returns a list of available data type names for this source."""
        print("Getting AWARE data types...", self.device_label)
        if self.status == "active" and self.device_id:
            tables = db_connector.get_aware_tables(self.device_label)
            return tables if tables else []
        return []

    def fetch_data(self, data_type="battery", timestamp=0, limit=1000):
        """Get's the users data from the AWARE server"""
        print("Getting AWARE data...", self.device_label)
        if self.status == "active" and self.device_id:
            return db_connector.get_aware_data(
                self.device_label, data_type, timestamp=timestamp, limit=limit
            )
        return []

    def count_rows(self, data_type="battery", start_date=None, end_date=None):
        """Return the number of rows available for the given AWARE data_type."""
        if self.status == "active" and self.device_id:
            return db_connector.get_aware_count(
                self.device_label, data_type, start_date, end_date
            )
        return 0

    def supports_deletion(self):
        return True

    def latest_timestamp(self, data_type, start_date=None, end_date=None):
        if self.status == "active" and self.device_id:
            return db_connector.get_aware_max_timestamp(
                self.device_label, data_type, start_date, end_date
            )
        return None

    def mark_deletable(self, data_type, through):
        """Queue an AWARE deletion request for all rows with timestamp <= through."""
        # through is inclusive → daemon deletes timestamp < delete_before
        delete_before = int(through) + 1
        return db_connector.insert_deletion_request(
            self.device_label, data_type, delete_before
        )
