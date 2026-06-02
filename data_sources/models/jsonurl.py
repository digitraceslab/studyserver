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
        return "JSON URL Data"
    
    def get_data_types(self):
        return ["raw_json"]

    def fetch_data(self, data_type, limit=10000, start_date=None, end_date=None, offset=0):
        """Fetches and returns the JSON data from the source URL.
        
        No formatting or processing, just returns the string."""
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

            # Apply offset and limit slicing
            start = int(offset) if offset else 0
            end = start + int(limit) if limit is not None else None
            return enriched_data[start:end]
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
        