import logging

from fastapi import Header, HTTPException, Query, status
from prometheus_client import Counter, Gauge, Histogram

from app.config import get_settings

logger = logging.getLogger(__name__)

HUB_EVENTS_ROUTED = Counter("hub_events_routed_total", "Total events routed through the hub")
HUB_ALEXA_REQUESTS = Counter("hub_alexa_requests_total", "Total Alexa requests handled")
HUB_STORE_WRITES_DEGRADED = Counter(
    "hub_store_writes_degraded_total",
    "Total degraded store writes using unconditional fallback",
)
HUB_BEDROCK_CALLS = Counter("hub_bedrock_calls_total", "Total Bedrock Converse API calls")
HUB_BEDROCK_FALLBACKS = Counter("hub_bedrock_fallbacks_total", "Total Bedrock fallback invocations")
HUB_WS_CONNECTIONS = Gauge("hub_ws_connections", "Current active WebSocket connections")
HUB_ALEXA_LATENCY = Histogram(
    "hub_alexa_latency_seconds",
    "Alexa request handling latency in seconds",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 8.0),
)


class MetricsMeta(type):
    _total_alexa_latency_ms: float = 0.0

    @property
    def ws_connections(cls) -> float:
        return float(HUB_WS_CONNECTIONS._value.get())

    @ws_connections.setter
    def ws_connections(cls, val: float) -> None:
        HUB_WS_CONNECTIONS.set(float(val))

    @property
    def events_routed(cls) -> float:
        return float(HUB_EVENTS_ROUTED._value.get())

    @events_routed.setter
    def events_routed(cls, val: float) -> None:
        diff = float(val) - float(HUB_EVENTS_ROUTED._value.get())
        if diff > 0:
            HUB_EVENTS_ROUTED.inc(diff)

    @property
    def alexa_requests(cls) -> float:
        return float(HUB_ALEXA_REQUESTS._value.get())

    @alexa_requests.setter
    def alexa_requests(cls, val: float) -> None:
        diff = float(val) - float(HUB_ALEXA_REQUESTS._value.get())
        if diff > 0:
            HUB_ALEXA_REQUESTS.inc(diff)

    @property
    def bedrock_calls(cls) -> float:
        return float(HUB_BEDROCK_CALLS._value.get())

    @bedrock_calls.setter
    def bedrock_calls(cls, val: float) -> None:
        diff = float(val) - float(HUB_BEDROCK_CALLS._value.get())
        if diff > 0:
            HUB_BEDROCK_CALLS.inc(diff)

    @property
    def bedrock_fallbacks(cls) -> float:
        return float(HUB_BEDROCK_FALLBACKS._value.get())

    @bedrock_fallbacks.setter
    def bedrock_fallbacks(cls, val: float) -> None:
        diff = float(val) - float(HUB_BEDROCK_FALLBACKS._value.get())
        if diff > 0:
            HUB_BEDROCK_FALLBACKS.inc(diff)

    @property
    def hub_store_writes_degraded_total(cls) -> float:
        return float(HUB_STORE_WRITES_DEGRADED._value.get())

    @property
    def total_alexa_latency_ms(cls) -> float:
        return cls._total_alexa_latency_ms

    @total_alexa_latency_ms.setter
    def total_alexa_latency_ms(cls, val: float) -> None:
        added_latency = float(val) - cls._total_alexa_latency_ms
        if added_latency > 0:
            HUB_ALEXA_LATENCY.observe(added_latency / 1000.0)
        cls._total_alexa_latency_ms = float(val)


class Metrics(metaclass=MetricsMeta):
    """Facade maintaining backward compatibility for Metrics class while backing by Prometheus."""

    @classmethod
    def hub_store_writes_degraded_total_inc(cls) -> None:
        HUB_STORE_WRITES_DEGRADED.inc()

    @classmethod
    def get_metrics(cls) -> dict[str, float]:
        requests = cls.alexa_requests
        avg_latency = (cls.total_alexa_latency_ms / requests) if requests > 0 else 0.0
        return {
            "ws_connections": cls.ws_connections,
            "events_routed": cls.events_routed,
            "alexa_requests": cls.alexa_requests,
            "bedrock_calls": cls.bedrock_calls,
            "bedrock_fallbacks": cls.bedrock_fallbacks,
            "hub_store_writes_degraded_total": cls.hub_store_writes_degraded_total,
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
