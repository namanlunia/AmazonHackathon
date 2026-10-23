import json
import logging
import sys
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.config import get_settings
from app.routers import Metrics, alexa, devices
from app.state_store import get_store
from app.ws_manager import ws_manager


class JSONLogFormatter(logging.Formatter):
    """Formats log records as structured single-line JSON for CloudWatch."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        for attr in ("user_id", "device_id", "event_type"):
            val = getattr(record, attr, None)
            if val is not None:
                log_obj[attr] = val
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


def configure_logging(level_name: str) -> None:
    root = logging.getLogger()
    level = getattr(logging, level_name.upper(), logging.INFO)
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONLogFormatter())
    root.handlers = [handler]


SERVER_START_TIME: float = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global SERVER_START_TIME
    SERVER_START_TIME = time.time()
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)

    store = get_store(settings)
    if settings.USE_AWS:
        try:
            await store.ensure_table()
        except Exception as exc:
            logging.getLogger(__name__).warning(f"Failed to ensure DynamoDB table, continuing with fallback: {exc}")

    ws_manager.start_heartbeat()
    logging.getLogger(__name__).info(f"Amazon Continuity Hub started in {'AWS' if settings.USE_AWS else 'LOCAL'} mode")
    yield
    await ws_manager.stop_heartbeat()
    logging.getLogger(__name__).info("Amazon Continuity Hub shut down")


settings = get_settings()
app = FastAPI(
    title="Amazon Continuity Hub",
    description="Cross-device state synchronization and event-routing mesh",
    version="1.0.0",
    lifespan=lifespan,
)

# Wildcard origin with allow_credentials=True is disallowed by CORS specification
allow_creds = False if settings.CORS_ORIGINS == ["*"] else True
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=allow_creds,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(devices.router)
app.include_router(alexa.router)


@app.get("/health")
async def health_check() -> dict[str, Any]:
    current_settings = get_settings()
    uptime = time.time() - SERVER_START_TIME if SERVER_START_TIME > 0 else 0.0
    return {
        "status": "ok",
        "mode": "aws" if current_settings.USE_AWS else "local",
        "uptime_seconds": round(uptime, 2),
        "connections": ws_manager.online_count(),
    }


@app.get("/metrics")
async def metrics_endpoint() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/stats")
async def stats_endpoint() -> dict[str, Any]:
    return Metrics.get_metrics()
