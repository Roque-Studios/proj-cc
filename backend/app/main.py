from fastapi import FastAPI, Depends, Request, Response
import structlog
import redis
from .config import settings
from sqlalchemy import text
from .database import get_db
from .logger import setup_logging
from sqlalchemy.orm import Session
from . import cache
from .routers import auth, creator, subscriptions, viewer, webhooks

setup_logging()
logger = structlog.get_logger()

app = FastAPI(
    title="Content Creator Engine",
    version=settings.CC_VERSION,
    description="An open source platform for digital content creators and exclusive content.",
)

app.include_router(auth.router)
app.include_router(creator.router)
app.include_router(viewer.router)
app.include_router(webhooks.router)
app.include_router(subscriptions.router)


@app.middleware("http")
async def media_no_store(request: Request, call_next):
    """Never let clients cache media responses.

    Watermarked media is volatile (TTL-cached in Redis), so browsers/CDNs must
    not store it. Nginx enforces the same header on media paths for good measure.
    """
    response = await call_next(request)
    if request.url.path.startswith("/media"):
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


@app.api_route("/media/{media_id}", methods=["GET", "HEAD"])
def get_media(media_id: str):
    """Placeholder media endpoint.

    Serves bytes from the Redis watermarked-media cache, generating a dummy
    payload and caching it on first access. To be replaced by the real
    watermarking pipeline. Always responds with ``Cache-Control: no-store``
    (set by the media_no_store middleware).
    """
    data = cache.get_cached_watermarked_media(media_id)
    if data is None:
        logger.info("Media cache miss, generating placeholder", media_id=media_id)
        data = f"watermarked:{media_id}:placeholder".encode()
        cache.set_watermarked_media(media_id, data)
    else:
        logger.debug("Media served from cache", media_id=media_id)
    return Response(content=data, media_type="application/octet-stream")
