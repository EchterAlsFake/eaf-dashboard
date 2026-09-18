"""EAF administration dashboard."""


def create_app(*args, **kwargs):
    """Load the web application lazily so broker-only processes stay isolated."""
    from .app import create_app as factory

    return factory(*args, **kwargs)


__all__ = ["create_app"]
