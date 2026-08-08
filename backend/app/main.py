from fastapi import FastAPI, Depends, Request, Response
import structlog
import redis
from .config import settings
from sqlalchemy import text
from .database import get_db
from .logger import setup_logging
from sqlalchemy.orm import Session
from .routers import (
    admin,
    auth,
    content,
    creator,
    creator_content,
    creator_subscribers,
    engagement,
    media_public,
    messages,
    posts,
    public,
    realtime,
    stories,
    subscriptions,
    viewer,
    webhooks,
)

setup_logging()
logger = structlog.get_logger()

app = FastAPI(
    title="Content Creator Engine",
    version=settings.CC_VERSION,
    description="An open source platform for digital content creators and exclusive content.",
)

app.include_router(auth.router)
app.include_router(creator.router)
app.include_router(creator_content.router)
app.include_router(creator_subscribers.router)
app.include_router(viewer.router)
app.include_router(webhooks.router)
app.include_router(subscriptions.router)
app.include_router(posts.router)
app.include_router(engagement.router)
app.include_router(public.router)
app.include_router(stories.router)
app.include_router(stories.dashboard_router)
app.include_router(content.router)
app.include_router(media_public.router)
app.include_router(admin.router)
app.include_router(messages.router)
app.include_router(realtime.router)


@app.middleware("http")
async def media_no_store(request: Request, call_next):
    """Never let clients cache media responses.

    Watermarked media is volatile (TTL-cached in Redis), so browsers/CDNs must
    not store it. The content-media handler also sets the header directly;
    this middleware plus nginx enforce it on the whole path for good measure.
    """
    response = await call_next(request)
    if request.url.path.startswith("/content") or request.url.path.startswith(
        "/preview"
    ):
        response.headers["Cache-Control"] = "no-store"
    return response

@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    logger.debug("Health check endpoint called")
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
        logger.debug("Database health check passed")
    except Exception as e:
        logger.error("DB health check failed", error=str(e))
        db_status = "error"

    redis_status = "ok"
    try:
        r = redis.from_url(settings.REDIS_URL)
        r.ping()
        logger.debug("Redis health check passed")
    except Exception as e:
        logger.error("Redis health check failed", error=str(e))
        redis_status = "error"

    status = "ok" if db_status == "ok" and redis_status == "ok" else "error"
    logger.info("Health check completed", status=status, db=db_status, redis=redis_status)
    return {"status": status, "db": db_status, "redis": redis_status}


