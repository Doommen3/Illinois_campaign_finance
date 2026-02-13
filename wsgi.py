"""WSGI entry point for gunicorn / production servers."""
from webapp import create_app

app = create_app()
