"""Shared helpers used by views in several apps."""
import csv
from django.http import HttpResponse

def data_to_csv_response(data, filename):
    """Return ``data`` (a list of dicts with the same keys) as a CSV download.

    The header row comes from the first dict's keys; the response has
    ``Content-Disposition: attachment; filename="<filename>"``. An empty list
    returns a plain-text response "No data available" instead of a CSV.
    """
    if not data:
        return HttpResponse("No data available", content_type='text/plain')

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.DictWriter(response, fieldnames=data[0].keys())
    writer.writeheader()
    writer.writerows(data)

    return response