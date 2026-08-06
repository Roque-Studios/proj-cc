"""Celery application for the Content Creator Engine.

The broker and result backend are both backed by the Redis service defined in
docker-compose.yml (see ``Settings.celery_broker_url`` / ``.celery_result_backend``).
"""

from __future__ import annotations

import structlog
from celery import Celery

from .config import settings
from .logger import setup_logging

# Configure structlog so worker logs use the same renderer as the API process.
setup_logging()
logger = structlog.get_logger()

celery_app = Celery(
    "content_creator",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Time
    timezone="UTC",
    enable_utc=True,
    # Broker
    broker_connection_retry_on_startup=settings.BROKER_CONNECTION_RETRY_ON_STARTUP,
    # Task execution
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Results: keep them around for 1 day, then let Redis expire them.
    result_expires=86400,
    result_cache_max=1000,
)

logger.info(
    "Celery app configured",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
