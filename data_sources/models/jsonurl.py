"""JSON URL data source: rows fetched live from a participant-given URL."""

import uuid

from django.db import models
from django.urls import reverse
from .base import DataSource
import requests

class JsonUrlDataSource(DataSource):
    """Data source whose data is the JSON list served at ``url``.

    No setup or confirmation: active at once. The URL is fetched from the
    server on every call (10 s timeout), so whoever sets ``url`` makes the
    server send a GET request to it. Does not support deletion.
    """

    SOURCE_TYPE = "json_url"

    url = models.URLField(max_length=500, help_text="The URL where the JSON data can be fetched")

    @property
    def display_type(self):
        """User-friendly name for the data source type ("JSON URL Data")."""
        return self.display_type_for_configuration(self.configuration)

    @classmethod
    def display_type_for_configuration(cls, configuration):
        """Always "JSON URL Data"; the configuration is ignored."""
        return "JSON URL Data"

    def get_data_types(self):
        """Always ``["raw_json"]``."""
        return ["raw_json"]

    def fetch_data(self, data_type, timestamp=0, limit=1000):
        """Fetch the JSON data from the source URL.

        Filters to rows with ``int(row['timestamp']) >= timestamp``, sorts
        ascending by timestamp, then applies the soft-limit rule: up to
        ``limit`` rows are returned, extended to include all rows sharing the
        last timestamp so the caller can advance its cursor safely.

        json_url does not support deletion, so the soft-limit / cursor
        behaviour is best-effort.

        Rows are the served JSON objects (a single object is treated as a
        one-row list) with ``device_id`` set to this source's device_id,
        generated and saved on first use; a served ``device_id`` is kept as
        ``json_device_id``.

        Args:
            data_type: Must be "raw_json".
            timestamp: Unix-ms cursor; only rows at or after it are returned.
            limit: Soft maximum number of rows.

        Returns:
            The rows. Unlike ``DataSource.fetch_data`` it does not always
            return a list: ``(False, "No consent found.")`` without an active
            consent, ``{"error": ...}`` for a data type other than "raw_json"
            or when the request fails or the response is not JSON.

        Raises:
            KeyError: If a served object has no ``timestamp``.
            ValueError: If a ``timestamp`` is not int-convertible.
        """
        if not self.has_active_consent():
            return False, "No consent found."

        if data_type != 'raw_json':
            return {"error": "Invalid data type requested."}
        try:
            response = requests.get(self.url, timeout=10)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, list):
                # Response must be a list. Assuming this is a single object, wrap in a list.
                result = [result]

            # json_url has no real device, but downstream consumers expect a
            # stable per-source device_id on every row — generate one on first use.
            if not self.device_id:
                self.device_id = uuid.uuid4()
                self.save()

            enriched_data = []
            for row in result:
                if 'device_id' in row:
                    row['json_device_id'] = row['device_id']
                row['device_id'] = str(self.device_id)
                enriched_data.append(row)

            # Filter by cursor and sort ascending
            filtered = [r for r in enriched_data if int(r['timestamp']) >= int(timestamp)]
            filtered.sort(key=lambda r: int(r['timestamp']))

            # Apply soft limit
            if len(filtered) <= int(limit):
                return filtered

            batch = filtered[:int(limit)]
            last_ts_val = int(batch[-1]['timestamp'])

            # Extend to include all rows sharing last_ts_val
            extended = [r for r in batch if int(r['timestamp']) != last_ts_val]
            extended += [r for r in filtered if int(r['timestamp']) == last_ts_val]
            return extended

        except requests.exceptions.RequestException as e:
            return {"error": f"Could not fetch data from URL: {e}"}

    def count_rows(self, data_type, start_date=None, end_date=None):
        """Count the rows for the given data_type by fetching the JSON and counting entries.

        Does not check consent.

        Args:
            data_type: Must be "raw_json".
            start_date: Ignored.
            end_date: Ignored.

        Returns:
            The row count; 0 for an unknown data type or a failed request.
        """
        if data_type != 'raw_json':
            return 0

        try:
            response = requests.get(self.url, timeout=10)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, list):
                result = [result]
            return len(result)
        except requests.exceptions.RequestException:
            return 0
        