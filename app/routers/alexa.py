import logging
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Request

from app.bedrock_service import bedrock_service
from app.config import get_settings
from app.models import (
    AlexaRequest,
    AlexaResponse,
    CommandAction,
    ContextEvent,
    DeviceType,
    Envelope,
    EnvelopeType,
    PowerState,
    UnifiedState,
)
from app.routers import Metrics, verify_api_key
from app.state_store import get_store
from app.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["alexa"])


def _extract_slot_value(slots: dict[str, Any] | None, slot_name: str) -> str | None:
    if not slots or slot_name not in slots:
        return None
    slot_entry = slots[slot_name]
    if isinstance(slot_entry, dict):
        if "value" in slot_entry and isinstance(slot_entry["value"], str):
            return slot_entry["value"]
        if "slotValue" in slot_entry and isinstance(slot_entry["slotValue"], dict):
            return slot_entry["slotValue"].get("value")
    elif isinstance(slot_entry, str):
        return slot_entry
    return None


@router.post("/alexa", response_model=AlexaResponse)
async def handle_alexa_request(
    request: Request,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    _: None = Depends(verify_api_key),
) -> AlexaResponse:
    start_time = time.perf_counter()
    Metrics.alexa_requests += 1

    try:
        body = await request.json()
        alexa_req = AlexaRequest.model_validate(body)
    except Exception as parse_err:  # noqa: BLE001
        logger.warning(f"Error parsing Alexa request body: {parse_err}")
        return AlexaResponse.build("Sorry, I could not understand that request.", should_end_session=True)

    settings = get_settings()
    user_id = x_user_id
    if not user_id and alexa_req.session and alexa_req.session.user and alexa_req.session.user.userId:
        user_id = alexa_req.session.user.userId
    if not user_id:
        user_id = settings.DEFAULT_USER_ID

    store = get_store()
    req_type = alexa_req.request.type

    try:
        if req_type == "LaunchRequest":
            resp = AlexaResponse.build(
                "Welcome to Amazon Continuity Hub. You can ask what is open or tell me to continue on Fire TV.",
                should_end_session=False,
                reprompt_text="What would you like to do?",
            )

        elif req_type == "SessionEndedRequest":
            resp = AlexaResponse.build("Goodbye.", should_end_session=True)

        elif req_type == "IntentRequest":
            intent = alexa_req.request.intent
            intent_name = intent.name if intent else ""
            slots = intent.slots if intent else {}

            if intent_name in ("WhatsOpenIntent", "AskContextIntent"):
                query = _extract_slot_value(slots, "query") or "What's open right now?"
                state = await store.get(user_id)
                Metrics.bedrock_calls += 1
                speech, degraded = await bedrock_service.answer(query, state)
                if degraded:
                    Metrics.bedrock_fallbacks += 1
                resp = AlexaResponse.build(speech, should_end_session=True)

            elif intent_name == "ContinueOnFireTVIntent":
                state = await store.get(user_id)
                if not state.active_context or not state.active_context.url:
                    resp = AlexaResponse.build(
                        "There is no active video or page to continue.",
                        should_end_session=True,
                    )
                else:
                    fire_tv_devices = ws_manager.find_devices(user_id, DeviceType.fire_tv)
                    if not fire_tv_devices:
                        resp = AlexaResponse.build(
                            "Fire TV is currently offline.",
                            should_end_session=True,
                        )
                    else:
                        target_id = fire_tv_devices[0]
                        ctx = state.active_context
                        cmd_envelope = Envelope(
                            type=EnvelopeType.command,
                            source_device_id="alexa",
                            target_device_id=target_id,
                            payload={
                                "action": CommandAction.play.value,
                                "url": ctx.url,
                                "position_seconds": ctx.position_seconds,
                            },
                        )
                        await ws_manager.send_to_device(user_id, target_id, cmd_envelope)
                        Metrics.events_routed += 1

                        def record_event(st: UnifiedState) -> None:
                            st.history.append(
                                ContextEvent(
                                    source_device_id="alexa",
                                    target_device_id=target_id,
                                    description=f"Routed {ctx.title} to Fire TV at {ctx.position_seconds}s",
                                    timestamp=datetime.now(UTC).isoformat(),
                                )
                            )

                        await store.update(user_id, record_event)
                        pos_int = int(ctx.position_seconds)
                        title_clean = ctx.title[:50]
                        speech = f"Continuing {title_clean} on Fire TV at {pos_int} seconds."
                        resp = AlexaResponse.build(speech, should_end_session=True)

            elif intent_name == "SendToBrowserIntent":
                state = await store.get(user_id)
                url = None
                pos = 0.0
                title = "media"
                if state.active_context and state.active_context.url:
                    url = state.active_context.url
                    pos = state.active_context.position_seconds
                    title = state.active_context.title
                else:
                    for dev in state.devices.values():
                        if dev.device_type == DeviceType.fire_tv:
                            pl = dev.payload if isinstance(dev.payload, dict) else dev.payload.model_dump()
                            np = pl.get("now_playing")
                            if np and np.get("url"):
                                url = np["url"]
                                pos = np.get("position_seconds", 0.0)
                                title = np.get("title", "video")
                                break

                if not url:
                    resp = AlexaResponse.build(
                        "Nothing is currently playing to send to your browser.",
                        should_end_session=True,
                    )
                else:
                    firefox_devices = ws_manager.find_devices(user_id, DeviceType.firefox)
                    if not firefox_devices:
                        resp = AlexaResponse.build(
                            "Your browser is currently offline.",
                            should_end_session=True,
                        )
                    else:
                        target_id = firefox_devices[0]
                        cmd_envelope = Envelope(
                            type=EnvelopeType.command,
                            source_device_id="alexa",
                            target_device_id=target_id,
                            payload={
                                "action": CommandAction.open_url.value,
                                "url": url,
                                "position_seconds": pos,
                            },
                        )
                        await ws_manager.send_to_device(user_id, target_id, cmd_envelope)
                        Metrics.events_routed += 1
                        speech = f"Opened {title[:50]} in your browser."
                        resp = AlexaResponse.build(speech, should_end_session=True)

            elif intent_name == "SmartHomeIntent":
                device_slot = _extract_slot_value(slots, "device") or ""
                action_slot = _extract_slot_value(slots, "action") or ""
                state = await store.get(user_id)

                matched_device_id: str | None = None
                matched_name: str | None = None

                for dev_id, dev in state.devices.items():
                    if dev.device_type == DeviceType.smart_home:
                        pl = dev.payload if isinstance(dev.payload, dict) else dev.payload.model_dump()
                        name = pl.get("name", dev_id).lower()
                        if device_slot.lower() in name or name in device_slot.lower():
                            matched_device_id = dev_id
                            matched_name = pl.get("name", dev_id)
                            break

                if not matched_device_id:
                    resp = AlexaResponse.build(
                        f"Could not find a smart home device named {device_slot}."[:300],
                        should_end_session=True,
                    )
                else:
                    is_turn_off = "off" in action_slot.lower()
                    cmd_action = CommandAction.power_off if is_turn_off else CommandAction.power_on
                    new_power = PowerState.off if is_turn_off else PowerState.on
                    verb = "off" if is_turn_off else "on"

                    cmd_envelope = Envelope(
                        type=EnvelopeType.command,
                        source_device_id="alexa",
                        target_device_id=matched_device_id,
                        payload={"action": cmd_action.value},
                    )
                    await ws_manager.send_to_device(user_id, matched_device_id, cmd_envelope)
                    Metrics.events_routed += 1

                    def update_smart_state(st: UnifiedState) -> None:
                        if matched_device_id in st.devices:
                            cur_pl = st.devices[matched_device_id].payload
                            if isinstance(cur_pl, dict):
                                cur_pl["power"] = new_power.value
                            elif hasattr(cur_pl, "power"):
                                cur_pl.power = new_power
                        st.history.append(
                            ContextEvent(
                                source_device_id="alexa",
                                target_device_id=matched_device_id,
                                description=f"Switched {matched_name} {verb}",
                                timestamp=datetime.now(UTC).isoformat(),
                            )
                        )

                    await store.update(user_id, update_smart_state)
                    resp = AlexaResponse.build(f"Turned {verb} {matched_name}.", should_end_session=True)

            elif intent_name == "AMAZON.HelpIntent":
                resp = AlexaResponse.build(
                    "You can ask what is open on your browser, say continue on Fire TV, or turn devices on and off.",
                    should_end_session=False,
                )

            elif intent_name in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
                resp = AlexaResponse.build("Goodbye.", should_end_session=True)

            else:
                resp = AlexaResponse.build(
                    "I am not sure how to help with that. You can ask what is open or say continue on Fire TV.",
                    should_end_session=True,
                )
        else:
            resp = AlexaResponse.build("Request received.", should_end_session=True)

    except Exception:
        logger.exception("Unexpected Alexa handling error")
        resp = AlexaResponse.build(
            "Sorry, I encountered an issue processing your request.",
            should_end_session=True,
        )

    latency_ms = (time.perf_counter() - start_time) * 1000.0
    Metrics.total_alexa_latency_ms += latency_ms
    return resp
