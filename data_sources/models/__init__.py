"""Data source models: the DataSource base model and its concrete types.

Public API of the package:

- ``DataSource``: polymorphic base model. Its methods are what
  each source type implements and other apps (studies, users) call;
  see ``data_sources.models.base``.
- ``AwareDataSource``, ``JsonUrlDataSource``, ``NiimportDataSource``:
  the built-in source types. ``survey_datasource.models.SurveyDataSource``
  is a further type that lives in its own app.
- ``datasourceform``, ``datasourceconfigform``, ``datasourceadmin``:
  class decorators that register a type's participant form, admin
  StudySourceConfiguration form, and admin class.
- ``DeletableWatermark``: per (source, data type) download / deletable /
  deleted high-water marks.

A new source type subclasses ``DataSource``, sets ``SOURCE_TYPE``, and
registers a ModelForm with ``@datasourceform`` so the ``add_data_source``
view can create it.
"""

from .base import DataSource
from .niimport import NiimportDataSource
from .jsonurl import JsonUrlDataSource
from .aware import AwareDataSource
from .model_registration import datasourceform, datasourceconfigform, datasourceadmin
from .watermark import DeletableWatermark

