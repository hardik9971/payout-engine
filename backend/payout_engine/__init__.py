# Required to expose the Celery app so Django finds it automatically
from .celery import app as celery_app  # noqa: F401

__all__ = ("celery_app",)
