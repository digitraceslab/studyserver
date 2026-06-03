import os
import requests
from django.core.cache import cache
from django.template.loader import render_to_string
from django.conf import settings


def get_study_page_html(repo_url):
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

