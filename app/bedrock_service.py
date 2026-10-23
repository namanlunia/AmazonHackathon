import asyncio
import json
import logging
from typing import Any

import boto3
from botocore.config import Config

from app.config import Settings, get_settings
from app.models import DeviceType, UnifiedState

logger = logging.getLogger(__name__)


class BedrockService:
    """Answers user context queries via Bedrock Converse or rule-based mock AI."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self.settings.AWS_REGION,
                config=Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 1}),
            )
        return self._client

    def _compact_context(self, state: UnifiedState) -> dict[str, Any]:
        online_devices = {}
        for dev_id, dev in state.devices.items():
            if dev.online:
                payload_data = (
                    dev.payload.model_dump()
                    if hasattr(dev.payload, "model_dump")
                    else dev.payload
                )
                online_devices[dev_id] = {
                    "device_type": dev.device_type,
                    "payload": payload_data,
                }

        return {
            "active_context": (
                state.active_context.model_dump() if state.active_context else None
            ),
            "online_devices": online_devices,
            "recent_events": [
                event.model_dump() for event in state.history[-5:]
            ],
        }

    def _mock_answer(self, question: str, state: UnifiedState) -> str:
        q = question.lower()

        # Browser and tabs
        if any(w in q for w in ["tab", "open", "browser", "firefox"]):
            for dev in state.devices.values():
                if dev.device_type == DeviceType.firefox:
                    payload = (
                        dev.payload.model_dump()
                        if hasattr(dev.payload, "model_dump")
                        else dev.payload
                    )
                    tabs = payload.get("tabs", [])
                    if tabs:
                        active_tabs = [t for t in tabs if t.get("active")]
                        target_tab = active_tabs[0] if active_tabs else tabs[0]
                        title = target_tab.get("title", "Untitled")
                        return f"You have {title} open in your browser."
            return "There are no open tabs in your browser."

        # Fire TV and playback
        if any(w in q for w in ["playing", "watching", "tv", "video"]):
            for dev in state.devices.values():
                if dev.device_type == DeviceType.fire_tv:
                    payload = (
                        dev.payload.model_dump()
                        if hasattr(dev.payload, "model_dump")
                        else dev.payload
                    )
                    now_playing = payload.get("now_playing")
                    if now_playing and now_playing.get("title"):
                        return f"Fire TV is playing {now_playing['title']} on {now_playing.get('app', 'app')}."
            if state.active_context:
                return f"Currently active video is {state.active_context.title}."
            return "Nothing is currently playing on Fire TV."

        # Smart home
        if any(w in q for w in ["light", "lamp", "thermostat", "home", "smart"]):
            home_devices = []
            for dev in state.devices.values():
                if dev.device_type == DeviceType.smart_home:
                    payload = (
                        dev.payload.model_dump()
                        if hasattr(dev.payload, "model_dump")
                        else dev.payload
                    )
                    name = payload.get("name", dev.device_id)
                    power = payload.get("power", "unknown")
                    home_devices.append(f"{name} is {power}")
            if home_devices:
                return f"Smart home status: {', '.join(home_devices)}."
            return "No smart home devices are currently connected."

        # Continuity & active context
        if any(w in q for w in ["continue", "resume", "active"]):
            if state.active_context:
                return (
                    f"You were watching {state.active_context.title} at "
                    f"{int(state.active_context.position_seconds)} seconds."
                )
            return "Nothing is active to continue right now."

        # Default summary
        if state.active_context:
            return f"Your active session is {state.active_context.title}."
        return "Nothing is active right now."

    async def answer(self, question: str, state: UnifiedState) -> tuple[str, bool]:
        if not self.settings.USE_AWS:
            return self._mock_answer(question, state), False

        compact_context = self._compact_context(state)
        context_str = json.dumps(compact_context, separators=(",", ":"))
        system_prompt = (
            "Answer ONLY from the provided JSON context, max 2 short sentences, "
            "plain text for text-to-speech (no markdown or emoji), "
            "say you don't know if the context lacks the answer."
        )

        def _sync_bedrock() -> str:
            client = self._get_client()
            resp = client.converse(
                modelId=self.settings.BEDROCK_MODEL_ID,
                system=[{"text": system_prompt}],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"text": f"Context: {context_str}\nQuestion: {question}"}
                        ],
                    }
                ],
                inferenceConfig={"maxTokens": 200, "temperature": 0.2},
            )
            return resp["output"]["message"]["content"][0]["text"].strip()

        try:
            speech = await asyncio.wait_for(asyncio.to_thread(_sync_bedrock), timeout=4.5)
            return speech[:300], False
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"Bedrock converse failed or timed out: {exc}; falling back to mock",
                extra={"event_type": "bedrock_fallback"},
            )
            return self._mock_answer(question, state), True


bedrock_service = BedrockService()
