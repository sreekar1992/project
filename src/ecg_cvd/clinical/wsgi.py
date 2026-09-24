"""Gunicorn entry point for the authenticated platform API."""
from .app import create_app

app = create_app()
