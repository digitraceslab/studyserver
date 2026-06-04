from .base import DataSource


def _get_source_type(model_class):
    if not isinstance(model_class, type) or not issubclass(model_class, DataSource):
        raise TypeError("decorator model must be a DataSource subclass")
    source_type = getattr(model_class, "SOURCE_TYPE", None)
    if not source_type:
        raise ValueError("DataSource subclass must define a non-empty SOURCE_TYPE")
    return source_type


def datasourceform(model_class):
    """Decorator that registers a DataSource form class using model SOURCE_TYPE."""
    source_type = _get_source_type(model_class)

    def decorator(form_class):
        DataSource.register_form_class(source_type, form_class)
        return form_class

    return decorator


def datasourceconfigform(model_class):
    """Decorator that registers a config form class using model SOURCE_TYPE."""
    source_type = _get_source_type(model_class)

    def decorator(form_class):
        DataSource.register_config_form_class(source_type, form_class)
        return form_class

    return decorator

def datasourceadmin(model_class):
    """Decorator that registers a DataSource child model for parent admin routing."""
    def decorator(admin_class):
        DataSource.register_admin_child_model(model_class)
        return admin_class
    return decorator