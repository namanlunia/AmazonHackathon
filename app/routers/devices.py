import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel

from app.bedrock_service import bedrock_service
from app.config import get_settings
from app.models import (
    ActiveContext,
    Command,
    ContextEvent,
    ContextKind,
    DeviceState,
    DeviceType,
    Envelope,
    EnvelopeType,
    UnifiedState,
)
from app.routers import Metrics, verify_api_key
from app.state_store import get_store
from app.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["devices"])


class AskRequest(BaseModel):
    user_id: str
    question: str


def _apply_state_payload(
    state: UnifiedState,
    device_id: str,
    device_type: DeviceType,
    payload: dict[str, Any],
) -> None:
    now_iso = datetime.now(UTC).isoformat()
    existing_dev = state.devices.get(device_id)
    online_status = existing_dev.online if existing_dev else True
    state.devices[device_id] = DeviceState(
        device_id=device_id,
        device_type=device_type,
        online=online_status,
        last_seen=now_iso,
        payload=payload,
    )

    # Active context resolution
    if device_type == DeviceType.firefox:
        tabs = payload.get("tabs", [])
        for tab in tabs:
            if tab.get("active"):
                pos = tab.get("media_position_seconds")
                if pos is not None and pos > 0:
                    state.active_context = ActiveContext(
                        kind=ContextKind.video,
                        title=tab.get("title", "Video Tab"),
                        url=tab.get("url"),
                        position_seconds=float(pos),
                        source_device_id=device_id,
                        updated_at=now_iso,
                    )
                break
    elif device_type == DeviceType.fire_tv:
        now_playing = payload.get("now_playing")
        if now_playing and now_playing.get("state") in ("playing", "paused"):
            state.active_context = ActiveContext(
                kind=ContextKind.video,
                title=now_playing.get("title", "Fire TV Stream"),
                url=now_playing.get("url"),
                position_seconds=float(now_playing.get("position_seconds", 0.0)),
                source_device_id=device_id,
                updated_at=now_iso,
            )

    state.history.append(
        ContextEvent(
            source_device_id=device_id,
            description=f"State update from {device_id} ({device_type.value})",
            timestamp=now_iso,
            metadata={"payload_keys": list(payload.keys())},
        )
    )
    if len(state.history) > 20:
        state.history = state.history[-20:]


@router.websocket("/ws/{user_id}/{device_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    device_id: str,
    device_type: DeviceType = Query(...),  # noqa: B008
    api_key: str | None = Query(default=None),
) -> None:
    settings = get_settings()
    if settings.HUB_API_KEY and api_key != settings.HUB_API_KEY:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
        return

    await websocket.accept()
    Metrics.ws_connections += 1
    await ws_manager.connect(user_id, device_id, device_type, websocket)
    store = get_store()

    def mark_online(st: UnifiedState) -> None:
        now_iso = datetime.now(UTC).isoformat()
        if device_id in st.devices:
            st.devices[device_id].online = True
            st.devices[device_id].last_seen = now_iso
        else:
            st.devices[device_id] = DeviceState(
                device_id=device_id,
                device_type=device_type,
                online=True,
                last_seen=now_iso,
                payload={},
            )

    current_state = await store.update(user_id, mark_online)

    snapshot_env = Envelope(
        type=EnvelopeType.snapshot,
        source_device_id="server",
        target_device_id=device_id,
        payload=current_state.model_dump(),
    )
    await websocket.send_text(snapshot_env.model_dump_json())

    presence_env = Envelope(
        type=EnvelopeType.presence,
        source_device_id=device_id,
        payload={
            "device_id": device_id,
            "device_type": device_type.value,
            "online": True,
        },
    )
    await ws_manager.broadcast(user_id, presence_env, exclude=device_id)

    try:
        while True:
            raw_text = await websocket.receive_text()
            ws_manager.mark_alive(user_id, device_id)
            try:
                data = json.loads(raw_text)
                env = Envelope.model_validate(data)
            except Exception as err:  # noqa: BLE001
                err_env = Envelope(
                    type=EnvelopeType.error,
                    source_device_id="server",
                    target_device_id=device_id,
                    payload={"error": f"Invalid envelope format: {err}"},
                )
                await websocket.send_text(err_env.model_dump_json())
                continue

            if env.type == EnvelopeType.ping:
                pong_env = Envelope(
                    type=EnvelopeType.pong,
                    source_device_id="server",
                    target_device_id=device_id,
                    payload=env.payload,
                )
                await websocket.send_text(pong_env.model_dump_json())
            elif env.type == EnvelopeType.pong:
                ws_manager.mark_alive(user_id, device_id)
            elif env.type == EnvelopeType.state_update:
                payload_data = env.payload

                def create_state_updater(d: dict[str, Any]):
                    def _updater(st: UnifiedState) -> None:
                        _apply_state_payload(st, device_id, device_type, d)
                    return _updater

                await store.update(user_id, create_state_updater(payload_data))
                Metrics.events_routed += 1

                forward_env = Envelope(
                    type=EnvelopeType.state_update,
                    source_device_id=device_id,
                    payload=payload_data,
                )
                await ws_manager.broadcast(user_id, forward_env, exclude=device_id)

                ack_env = Envelope(
                    type=EnvelopeType.ack,
                    source_device_id="server",
                    target_device_id=device_id,
                    payload={"ref_id": env.id},
                )
                await websocket.send_text(ack_env.model_dump_json())
            elif env.type == EnvelopeType.command:
                if env.target_device_id:
                    sent = await ws_manager.send_to_device(
                        user_id, env.target_device_id, env
                    )
                    Metrics.events_routed += 1
                    if not sent:
                        err_env = Envelope(
                            type=EnvelopeType.error,
                            source_device_id="server",
                            target_device_id=device_id,
                            payload={"error": f"Target device {env.target_device_id} is offline"},
                        )
                        await websocket.send_text(err_env.model_dump_json())

    except (WebSocketDisconnect, Exception) as exc:  # noqa: BLE001
        logger.info(
            f"WebSocket closed for {device_id}: {exc}",
            extra={"user_id": user_id, "device_id": device_id, "event_type": "ws_disconnect"},
        )
    finally:
        await ws_manager.disconnect(user_id, device_id)

        def mark_offline(st: UnifiedState) -> None:
            if device_id in st.devices:
                st.devices[device_id].online = False
                st.devices[device_id].last_seen = datetime.now(UTC).isoformat()

        await store.update(user_id, mark_offline)

        offline_env = Envelope(
            type=EnvelopeType.presence,
            source_device_id=device_id,
            payload={
                "device_id": device_id,
                "device_type": device_type.value,
                "online": False,
            },
        )
        await ws_manager.broadcast(user_id, offline_env)


@router.get("/api/v1/state/{user_id}", response_model=UnifiedState)
async def get_state(
    user_id: str,
    _: None = Depends(verify_api_key),
) -> UnifiedState:
    store = get_store()
    return await store.get(user_id)


@router.put("/api/v1/devices/{user_id}/{device_id}/state", response_model=UnifiedState)
async def update_device_state(
    user_id: str,
    device_id: str,
    payload: dict[str, Any],
    device_type: DeviceType = Query(...),  # noqa: B008
    _: None = Depends(verify_api_key),
) -> UnifiedState:
    store = get_store()

    def mutator(st: UnifiedState) -> None:
        _apply_state_payload(st, device_id, device_type, payload)

    updated = await store.update(user_id, mutator)
    Metrics.events_routed += 1

    env = Envelope(
        type=EnvelopeType.state_update,
        source_device_id=device_id,
        payload=payload,
    )
    await ws_manager.broadcast(user_id, env, exclude=device_id)
    return updated


@router.post("/api/v1/devices/{user_id}/{device_id}/command")
async def send_command(
    user_id: str,
    device_id: str,
    command: Command,
    _: None = Depends(verify_api_key),
) -> dict[str, Any]:
    env = Envelope(
        type=EnvelopeType.command,
        source_device_id="rest_api",
        target_device_id=device_id,
        payload=command.model_dump(),
    )
    delivered = await ws_manager.send_to_device(user_id, device_id, env)
    if not delivered:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Device {device_id} is offline or not found",
        )
    Metrics.events_routed += 1
    return {"status": "delivered", "envelope_id": env.id}


@router.post("/api/v1/ask")
async def ask_question(
    req: AskRequest,
    _: None = Depends(verify_api_key),
) -> dict[str, Any]:
    store = get_store()
    state = await store.get(req.user_id)
    Metrics.bedrock_calls += 1
    speech, degraded = await bedrock_service.answer(req.question, state)
    if degraded:
        Metrics.bedrock_fallbacks += 1
    return {"speech": speech, "degraded": degraded}


@router.delete("/api/v1/state/{user_id}")
async def reset_user_state(
    user_id: str,
    _: None = Depends(verify_api_key),
) -> dict[str, Any]:
    store = get_store()
    await store.reset(user_id)
    return {"status": "reset", "user_id": user_id}
