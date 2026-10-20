"""Client for the portability-server REST API."""
import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30
RATE_LIMIT_WAIT_SECONDS = 10


class PortabilityClient:
    """Researcher API client that holds a portability-server session token."""

    def __init__(self):
        self.session_token = None

    def base_url(self):
        return f"{settings.PORTABILITY_SERVER_URL}/api"

    def login(self):
        """Exchange the researcher token for a session token, store and return it.

        On 429 (rate limited) waits RATE_LIMIT_WAIT_SECONDS and retries once.
        """
        url = f"{self.base_url()}/session/"
        payload = {"token": settings.PORTABILITY_SERVER_TOKEN}
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if response.status_code == 429:
            time.sleep(RATE_LIMIT_WAIT_SECONDS)
            response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        self.session_token = response.json()["session_token"]
        return self.session_token

    def logout(self):
        """End the current session on the server and forget the session token."""
        if self.session_token is None:
            return
        response = requests.delete(
            f"{self.base_url()}/session/", headers=self._headers(), timeout=REQUEST_TIMEOUT
        )
        self.session_token = None
        response.raise_for_status()

    def _headers(self):
        if self.session_token is None:
            self.login()
        return {"Authorization": f"Token {self.session_token}"}

    def get(self, path, params=None):
        """GET an API path relative to base_url(). Returns the response dict.

        On 401 (expired or revoked session) logs in again and retries once.
        On 429 (rate limited) waits RATE_LIMIT_WAIT_SECONDS and retries once.
        """
        url = f"{self.base_url()}/{path}"
        response = requests.get(
            url, params=params, headers=self._headers(), timeout=REQUEST_TIMEOUT
        )
        if response.status_code == 401:
            self.login()
            response = requests.get(
                url, params=params, headers=self._headers(), timeout=REQUEST_TIMEOUT
            )
        elif response.status_code == 429:
            time.sleep(RATE_LIMIT_WAIT_SECONDS)
            response = requests.get(
                url, params=params, headers=self._headers(), timeout=REQUEST_TIMEOUT
            )
        response.raise_for_status()
        return response.json()

    def post(self, path, json=None):
        """POST JSON to an API path relative to base_url(). Returns the response dict.

        On 401 (expired or revoked session) logs in again and retries once.
        On 429 (rate limited) waits RATE_LIMIT_WAIT_SECONDS and retries once.
        """
        url = f"{self.base_url()}/{path}"
        response = requests.post(
            url, json=json, headers=self._headers(), timeout=REQUEST_TIMEOUT
        )
        if response.status_code == 401:
            self.login()
            response = requests.post(
                url, json=json, headers=self._headers(), timeout=REQUEST_TIMEOUT
            )
        elif response.status_code == 429:
            time.sleep(RATE_LIMIT_WAIT_SECONDS)
            response = requests.post(
                url, json=json, headers=self._headers(), timeout=REQUEST_TIMEOUT
            )
        response.raise_for_status()
        return response.json()

    def delete(self, path):
        """DELETE an API path relative to base_url().

        On 401 (expired or revoked session) logs in again and retries once.
        On 429 (rate limited) waits RATE_LIMIT_WAIT_SECONDS and retries once.
        """
        url = f"{self.base_url()}/{path}"
        response = requests.delete(url, headers=self._headers(), timeout=REQUEST_TIMEOUT)
        if response.status_code == 401:
            self.login()
            response = requests.delete(
                url, headers=self._headers(), timeout=REQUEST_TIMEOUT
            )
        elif response.status_code == 429:
            time.sleep(RATE_LIMIT_WAIT_SECONDS)
            response = requests.delete(
                url, headers=self._headers(), timeout=REQUEST_TIMEOUT
            )
        response.raise_for_status()

    def create_donation(self, source_type, data_start_date=None, data_end_date=None,
                        requested_data_types=None):
        """Create a donation on the portability server. Returns the response dict."""
        payload = {"source_type": source_type}
        if data_start_date:
            payload["data_start_date"] = str(data_start_date)
        if data_end_date:
            payload["data_end_date"] = str(data_end_date)
        if requested_data_types is not None:
            payload["requested_data_types"] = requested_data_types
        return self.post("donations/", json=payload)

    def get_donation(self, donation_id):
        """Get donation status by PK. Returns the response dict."""
        return self.get(f"donations/{donation_id}/")

    def get_data(self, donation_id, data_type=None, start_date=None, end_date=None,
                 limit=1000, offset=0):
        """Fetch processed data from a donation. Returns the response dict."""
        params = {}
        if data_type:
            params["data_type"] = data_type
        if start_date:
            params["start_date"] = str(start_date)
        if end_date:
            params["end_date"] = str(end_date)
        if limit:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
        return self.get(f"donations/{donation_id}/data/", params=params)

    def delete_donation(self, donation_id):
        """Delete (revoke) a donation on the portability server."""
        self.delete(f"donations/{donation_id}/")
