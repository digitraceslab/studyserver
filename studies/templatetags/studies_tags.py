"""Study template tags and filters for site templates.

Researcher API: researcher-written study page HTML (``Study.study_page_html``)
uses exactly one interface,
``{% include join_or_login_section %}``, which shows the join, login or
"enrolled" block. No ``{% load %}`` is needed for it. Nothing else in the
template context, including model methods or fields reached through
``study`` or ``user``, is part of the researcher API. When researcher HTML
needs something new, add it as a tag here.

The tags below are for the site's own templates, not researcher HTML.
"""

from django import template

register = template.Library()

MISSING_ASSET_PLACEHOLDER = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='200' height='60'>"
    "<rect width='100%25' height='100%25' fill='%23eee' stroke='%23999'/>"
    "<text x='50%25' y='50%25' dominant-baseline='middle' text-anchor='middle' "
    "font-family='sans-serif' font-size='12' fill='%23666'>Asset not configured</text>"
    "</svg>"
)


@register.filter
def asset_url(assets_dict, asset_name):
    """
    Get the URL for a named asset from the assets dictionary.

    Usage in template:
        {{ assets|asset_url:"study_logo.png" }}

    Returns:
        The asset URL, or None if the asset is not found in the local assets.
    """
    if isinstance(assets_dict, dict):
        return assets_dict.get(asset_name)
    return None


@register.simple_tag
def get_asset(assets_dict, asset_name, fallback_url=None):
    """
    Get the URL for a named asset from the assets dictionary with optional fallback.

    Usage in template:
        {% get_asset assets "study_logo.png" %}

    Args:
        assets_dict: The assets dictionary.
        asset_name: The name of the asset.
        fallback_url: The URL to return when the asset is not found. When not
            given, an SVG data URI reading "Asset not configured" is
            returned, so an ``<img>`` pointing at a missing asset shows that
            instead of a broken image.

    Returns:
        The asset URL if found, otherwise the fallback URL.
    """
    if fallback_url is None:
        fallback_url = MISSING_ASSET_PLACEHOLDER
    if isinstance(assets_dict, dict):
        asset_url = assets_dict.get(asset_name)
        if asset_url:
            return asset_url
    return fallback_url


@register.simple_tag
def study_custom_css():
    """
    The study's custom CSS text, or an empty string if not set.

    Usage in template:
        {% study_custom_css as custom_css %}
    """
    from studies.models import Study

    study = Study.objects.first()
    if study:
        return study.custom_css
    return ""
