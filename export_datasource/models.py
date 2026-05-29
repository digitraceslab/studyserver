from django.db import models
from data_sources.models import DataSource 


class ExportDataSource(DataSource):
    """Data source for data dumps from third party services that are uploaded by the user.
    
    Different services store data differently, so any processing depends on the which
    third party service the data is from. Processing is implemented separately for each
    service.

    A single source allows multiple uploads, which are processed separately. No setup is
    required and a link to the upload form is always displayed. If the researcher
    specifies a number of uploads to expect, the source is considered complete when all
    expected uploads are received.

    Once data is processed, the generated files are listed in processed_files. Each entry
    is a dictionary with the following keys
    - type: the type of data in the file, e.g. "google_location", "tiktok_activity"
    - path: the path to the file on the server

    The service is determined by the researcher when they set up the data source.
    Currently supported services are:
    - other: no processing, just store the data as it is. Researchers cannot download the
      data without help from the system administrators.
    """ 

    display_type = "Exported Data"

    # list of uploads expected with opening dates
    expected_uploads = models.JSONField(blank=True, null=True)
    uploaded_files = models.JSONField(blank=True, null=True)

    class Meta:
        verbose_name = "Export Data Source"
        verbose_name_plural = "Export Data Sources"

    def get_setup_url(self):
        return f"/export_datasource/{self.id}/setup/"

    def show_link(self):
        """ Link to the upload form. """
        return (f"/export_datasource/{self.id}/upload/", "Upload data")
    
    def get_data_types(self):
        """ Return the service as the data type. """
        for file in self.uploaded_files or []:
            if 'processed_files' in file:
                return file['processed_files']['type']
        return 

