"""WSGI entry point for production servers such as Gunicorn."""

from app import app

__all__ = ["app"]
