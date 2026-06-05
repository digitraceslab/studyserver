"""Client for the portability-server REST API."""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


def _base_url():
    return f"{settings.PORTABILITY_SERVER_URL}/api/donations"


def _headers():
    return {"Authorization": f"Token {settings.PORTABILITY_SERVER_TOKEN}"}


def create_donation(source_type, data_start_date=None, data_end_date=None,
                    requested_data_types=None):
    """Create a donation on the portability server. Returns the response dict."""
    payload = {"source_type": source_type}
    if data_start_date:
        payload["data_start_date"] = str(data_start_date)
    if data_end_date:
        payload["data_end_date"] = str(data_end_date)
    if requested_data_types is not None:
        payload["requested_data_types"] = requested_data_types
    response = requests.post(
        f"{_base_url()}/", json=payload, headers=_headers(), timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def get_donation(donation_id):
    """Get donation status by PK. Returns the response dict."""
    response = requests.get(
        f"{_base_url()}/{donation_id}/", headers=_headers(), timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def get_data(donation_id, data_type=None, start_date=None, end_date=None,
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
    response = requests.get(
        f"{_base_url()}/{donation_id}/data/",
        params=params, headers=_headers(), timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def delete_donation(donation_id):
    """Delete (revoke) a donation on the portability server."""
    response = requests.delete(
        f"{_base_url()}/{donation_id}/", headers=_headers(), timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
