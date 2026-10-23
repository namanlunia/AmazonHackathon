import logging

from fastapi import Header, HTTPException, Query, status

from app.config import get_settings

logger = logging.getLogger(__name__)


class Metrics:
    ws_connections: int = 0
    events_routed: int = 0
    alexa_requests: int = 0
    bedrock_calls: int = 0
    bedrock_fallbacks: int = 0
    total_alexa_latency_ms: float = 0.0

    @classmethod
    def get_metrics(cls) -> dict[str, float]:
        avg_latency = (
            (cls.total_alexa_latency_ms / cls.alexa_requests)
            if cls.alexa_requests > 0
            else 0.0
        )
        return {
            "ws_connections": float(cls.ws_connections),
            "events_routed": float(cls.events_routed),
            "alexa_requests": float(cls.alexa_requests),
            "bedrock_calls": float(cls.bedrock_calls),
            "bedrock_fallbacks": float(cls.bedrock_fallbacks),
            "avg_alexa_latency_ms": round(avg_latency, 2),
        }


def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    api_key: str | None = Query(default=None),
) -> None:
    settings = get_settings()
    if not settings.HUB_API_KEY:
        return

    provided = x_api_key or api_key
    if provided != settings.HUB_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key",
        )
