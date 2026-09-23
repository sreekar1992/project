"""Production WSGI entry point for a single ECG Workbench instance.

Gunicorn imports this module in the container. Deployment configuration is read
from the ECG_PROJECT_ROOT and ECG_* environment variables in ``create_app``.
"""
from .gui import create_app


app = create_app()
