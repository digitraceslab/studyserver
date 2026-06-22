"""Tracks per-(data_source, data_type) download/deletable/deleted watermarks."""

from django.db import models


class DeletableWatermark(models.Model):
    """Records how far data has been downloaded, marked deletable, and deleted for each data type within a data source."""

    data_source = models.ForeignKey(
        'data_sources.DataSource',
        on_delete=models.CASCADE,
        related_name='watermarks',
    )
    data_type = models.CharField(max_length=255)
    downloaded_through = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Max row timestamp (Unix time in milliseconds) served to researchers so far.",
    )
    deletable_through = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Unix time in milliseconds up to which a researcher has marked data deletable.",
    )
    deleted_through = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Unix time in milliseconds up to which the source confirms data has been purged.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (('data_source', 'data_type'),)
        indexes = [
            models.Index(fields=['data_type']),
        ]

    def __str__(self):
        return f"{self.data_source} / {self.data_type}"
