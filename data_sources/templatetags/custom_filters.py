"""Template filters of the data_sources app."""

from django import template
register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Look up a dictionary value by a key variable in templates.

    Args:
        dictionary: The dictionary to read.
        key: The key to look up.

    Returns:
        The value for ``key``, or None when missing.
    """
    return dictionary.get(key)