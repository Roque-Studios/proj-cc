from fastapi import FastAPI, Depends
import structlog
import redis
from .config import settings
from sqlalchemy import text
from .database import get_db
from .logger import setup_logging
from sqlalchemy.orm import Session

setup_logging()
logger = structlog.get_logger()

app = FastAPI(
    title="Content Creator Engine",
    version=settings.CC_VERSION,
    description="An open source platform for digital content creators and exclusive content.",
)

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
