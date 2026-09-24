"""Authenticated ECG research/clinical-decision-support platform.

This package is deliberately separate from the localhost-only research
workbench.  It preserves the existing ECG model while adding a tenant-aware
clinical workflow.  It is not a regulated medical device or production
clinical deployment.
"""

def create_app(*args, **kwargs):
    """Lazy application-factory import keeps model/migration imports lightweight."""
    from .app import create_app as factory
    return factory(*args, **kwargs)


__all__ = ["create_app"]
