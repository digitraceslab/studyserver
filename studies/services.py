"""Consent page template lookup.

Consent texts are defined per source configuration
(``StudySourceConfiguration.consent_template_html``); when one is empty the
default ``templates/studies/consent_default.html`` is used.
"""

import os
from django.conf import settings


def _get_default_consent_template():
    template_path = os.path.join(settings.BASE_DIR, 'templates/studies/consent_default.html')
    with open(template_path, 'r') as f:
        return f.read()


def get_consent_template(study, source_type, source_configuration=None):
    """Return the consent page template source for one source type.

    Order: ``source_configuration.consent_template_html`` when set; else the
    default ``templates/studies/consent_default.html``.

    Args:
        study: The study the consent page belongs to (currently unused).
        source_type: The source type the consent page is for (currently unused).
        source_configuration: Optional source configuration whose
            ``consent_template_html`` is used when set.

    Returns:
        The consent page template source.
    """
    if source_configuration is not None and source_configuration.consent_template_html:
        return source_configuration.consent_template_html
    return _get_default_consent_template()
