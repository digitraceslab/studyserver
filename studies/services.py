"""Study content fetched from the study's configuration repository.

Pages and consent texts can live in a config repository (``Study.config_url``).
These helpers fetch them over HTTP with a 5 s timeout and cache successful
responses for 300 s.
"""

import os
import requests
from django.core.cache import cache
from django.template.loader import render_to_string
from django.conf import settings


def get_study_page_html(repo_url):
    """Return the study front page HTML from ``<repo_url>/front_page.html``.

    Never raises: returns a "not configured" heading when ``repo_url`` is
    empty, and an error heading with the exception text when the fetch fails.

    Args:
        repo_url: The raw content base URL (``Study.raw_content_base_url``).

    Returns:
        A Django template source; the caller renders it.
    """
    if not repo_url:
        return "<h2>Study page has not been configured.</h2>"
    
    page_url = f"{repo_url}/front_page.html"
    
    cache_key = f'study_page_html_{repo_url}'
    cached_html = cache.get(cache_key)
    if cached_html:
        return cached_html
    try:
        response = requests.get(page_url, timeout=5)
        response.raise_for_status()
        html_content = response.text
        cache.set(cache_key, html_content, 300) 
        return html_content
    except requests.RequestException as e:
        return f"<h2>Error fetching study page: {e}</h2>"


def _get_default_consent_template():
    template_path = os.path.join(settings.BASE_DIR, 'templates/studies/consent_default.html')
    with open(template_path, 'r') as f:
        return f.read()

def get_consent_template(study, source_type, source_configuration=None):
    """Return the consent page template source for one source type.

    Order: ``source_configuration.consent_template_html`` when set; else
    ``consent_<source_type lowercased>.html`` from the study's config
    repository (cached per study and source type); else the default
    ``templates/studies/consent_default.html`` (also used when the study has
    no config repository or the fetch fails). Never raises for fetch errors.

    Args:
        study: The study whose config repository is consulted.
        source_type: The source type the consent page is for.
        source_configuration: Optional source configuration whose
            ``consent_template_html`` takes precedence when set.

    Returns:
        The consent page template source.
    """
    # A per-source-configuration consent template, when set by the researcher,
    # takes precedence over the repo-fetched template.
    if source_configuration is not None and source_configuration.consent_template_html:
        return source_configuration.consent_template_html

    if not study.raw_content_base_url:
        return _get_default_consent_template()

    template_url = f"{study.raw_content_base_url}/consent_{source_type.lower()}.html"

    cache_key = f'consent_template_{study.id}_{source_type}'
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        response = requests.get(template_url, timeout=5)
        response.raise_for_status()
        template = response.text
        cache.set(cache_key, template, 300)
        return template
    except requests.RequestException:
        return _get_default_consent_template()

