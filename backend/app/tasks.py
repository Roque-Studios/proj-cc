"""Celery tasks for the Content Creator Engine.

``debug_ping`` and ``debug_add`` are lightweight test tasks used to verify the
broker -> worker -> result-backend pipeline (e.g. the docker-compose acceptance
check). Real workloads (e.g. watermarking media) will be added as tasks here.
"""

from __future__ import annotations

import structlog

from .celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="tasks.debug_ping", bind=True)
def debug_ping(self, message: str = "ping") -> dict:
    """Return the message back — proves the task was enqueued and executed."""
    logger.info("debug_ping executed", task_id=self.request.id, message=message)
    return {"status": "ok", "message": message, "task_id": self.request.id}


@celery_app.task(name="tasks.debug_add")
def debug_add(x: int, y: int) -> int:
    """Sum two integers — proves arguments round-trip through the broker."""
    result = x + y
    logger.info("debug_add executed", x=x, y=y, result=result)
    return result
