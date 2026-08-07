from django import template

register = template.Library()


@register.filter
def asset_url(assets_dict, asset_name):
    """
    Get the URL for a named asset from the assets dictionary.
    
    Usage in template:
        {{ assets|asset_url:"study_logo.png" }}
    
    If the asset is not found in the local assets, returns None.
    """
    if isinstance(assets_dict, dict):
        return assets_dict.get(asset_name)
    return None


@register.simple_tag
def get_asset(assets_dict, asset_name, fallback_url=None):
    """
    Get the URL for a named asset from the assets dictionary with optional fallback.
    
    Usage in template:
        {% get_asset assets "study_logo.png" config_repository|add:"study_logo.png" %}
    
    Returns the asset URL if found, otherwise returns the fallback URL.
    """
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
