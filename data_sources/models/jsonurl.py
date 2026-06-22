from django.db import models
from django.urls import reverse
from .base import DataSource
import requests

class JsonUrlDataSource(DataSource):
    SOURCE_TYPE = "json_url"
    
    url = models.URLField(max_length=500, help_text="The URL where the JSON data can be fetched")

    @property
    def display_type(self):
        """Returns a user-friendly name for the data source type."""
        return self.display_type_for_configuration(self.configuration)

    @classmethod
    def display_type_for_configuration(cls, configuration):
        return "JSON URL Data"
    
    def get_data_types(self):
        return ["raw_json"]

    def fetch_data(self, data_type, timestamp=0, limit=1000):
        """Fetches and returns the JSON data from the source URL.

        Filters to rows with ``int(row['timestamp']) >= timestamp``, sorts
        ascending by timestamp, then applies the soft-limit rule: up to
        ``limit`` rows are returned, extended to include all rows sharing the
        last timestamp so the caller can advance its cursor safely.

        json_url does not support deletion, so the soft-limit / cursor
        behaviour is best-effort."""
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
        """Return number of rows for the given data_type. This will fetch the JSON and count entries."""
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
        