"""Class decorators that register a data source type's forms and admin."""

from .base import DataSource


def _get_source_type(model_class):
    """Return the SOURCE_TYPE of a DataSource subclass, raising if invalid."""
    if not isinstance(model_class, type) or not issubclass(model_class, DataSource):
        raise TypeError("decorator model must be a DataSource subclass")
    source_type = getattr(model_class, "SOURCE_TYPE", None)
    if not source_type:
        raise ValueError("DataSource subclass must define a non-empty SOURCE_TYPE")
    return source_type


def datasourceform(model_class):
    """Class decorator that registers a DataSource form class using the model's SOURCE_TYPE.

    Args:
        model_class: DataSource subclass with a non-empty SOURCE_TYPE.

    Returns:
        A decorator for the form class; it registers the form and returns it
        unchanged.

    Raises:
        TypeError: If ``model_class`` is not a DataSource subclass.
        ValueError: If it has no SOURCE_TYPE.
    """
    source_type = _get_source_type(model_class)

    def decorator(form_class):
        DataSource.register_form_class(source_type, form_class)
        return form_class

    return decorator


def datasourceconfigform(model_class):
    """Class decorator that registers a config form class using the model's SOURCE_TYPE.

    Args:
        model_class: DataSource subclass with a non-empty SOURCE_TYPE.

    Returns:
        A decorator for the form class; it registers the form and returns it
        unchanged.

    Raises:
        TypeError: If ``model_class`` is not a DataSource subclass.
        ValueError: If it has no SOURCE_TYPE.
    """
    source_type = _get_source_type(model_class)

    def decorator(form_class):
        DataSource.register_config_form_class(source_type, form_class)
        return form_class

    return decorator

def datasourceadmin(model_class):
    """Class decorator that registers a DataSource child model for parent admin routing.

    Args:
        model_class: Concrete DataSource subclass to register.

    Returns:
        A decorator for the admin class; it registers ``model_class`` and
        returns the admin class unchanged.
    """
    def decorator(admin_class):
        DataSource.register_admin_child_model(model_class)
        return admin_class
    return decorator