"""Top-level URL map of the study server: the site's public API.

Every user-facing page and data endpoint is reached through these prefixes;
each included app documents its own routes and their access rules.

- ``/`` (``home``): front page, see ``users.views.home``.
- ``/admin/``: Django admin (staff only).
- ``/accounts/``: ``users.urls`` (login, signup, dashboards, personal data
  API) and ``django.contrib.auth.urls`` (logout, password change/reset).
- ``/studies/``: ``studies.urls`` (study page, join/withdraw, consent
  workflow, legal pages, researcher data APIs).
- ``/data-sources/``: ``data_sources.urls`` (participants' data sources).
- ``/download/<path>/`` (``download_static_file``): file download from
  STATIC_ROOT, see ``study_server.views``.
- ``/survey/``: ``survey_extensions.urls`` first (disables the CSV export),
  then the survey library's ``survey.urls``.

In DEBUG, MEDIA_URL is also served from MEDIA_ROOT.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from users import views as user_views
from study_server import views as study_server_views


urlpatterns = [
    path('admin/', admin.site.urls),
    path('', user_views.home, name='home'),
    path('accounts/', include('users.urls')),
    path('accounts/', include('django.contrib.auth.urls')),
    path('studies/', include('studies.urls')),
    path('data-sources/', include('data_sources.urls')),
    path('download/<path:file_path>/', study_server_views.download_static_file, name='download_static_file'),
    path('survey/', include('survey_extensions.urls')),
    path('survey/', include('survey.urls')),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
