"""
Celery application entry point.
"""
import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "payout_engine.settings")

app = Celery("payout_engine")

# Load config from Django settings using CELERY_ namespace prefix
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks in installed apps
app.autodiscover_tasks()
