"""Gunicorn configuration for Render deployment (512MB free tier)."""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
workers = 1          # Single worker saves ~100MB on 512MB free tier
threads = 4          # Threads handle concurrency within the worker
timeout = 120
accesslog = "-"
errorlog = "-"
loglevel = "info"
preload_app = True
