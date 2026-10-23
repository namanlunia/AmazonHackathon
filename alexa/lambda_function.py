import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Stdlib-only forwarder bridging Alexa Skills Kit requests to the Continuity Hub."""
    hub_url = os.environ.get("HUB_URL", "http://localhost:8000")
    hub_api_key = os.environ.get("HUB_API_KEY", "")

    endpoint = f"{hub_url.rstrip('/')}/alexa"
    payload_bytes = json.dumps(event).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Alexa-Continuity-Forwarder/1.0",
    }
    if hub_api_key:
        headers["X-API-Key"] = hub_api_key

    req = urllib.request.Request(  # noqa: S310
        url=endpoint,
        data=payload_bytes,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=6.0) as resp:  # noqa: S310
            status_code = resp.getcode()
            response_body = resp.read().decode("utf-8")
            if status_code == 200:
                return json.loads(response_body)
            logger.error(f"Hub returned unexpected status code {status_code}: {response_body}")
    except urllib.error.HTTPError as http_err:
        err_msg = http_err.read().decode("utf-8", errors="ignore")
        logger.error(f"HTTPError contacting hub: {http_err.code} - {err_msg}")
    except Exception as exc:
        logger.error(f"Failed to communicate with Continuity Hub: {exc}")

    # Fallback response so Alexa speaks gracefully without skill failure
    return {
        "version": "1.0",
        "response": {
            "outputSpeech": {
                "type": "PlainText",
                "text": "Sorry, the Continuity Hub is temporarily unavailable.",
            },
            "shouldEndSession": True,
        },
    }
