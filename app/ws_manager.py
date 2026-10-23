import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import WebSocket

from app.models import DeviceType, Envelope, EnvelopeType

logger = logging.getLogger(__name__)


@dataclass
class DeviceConnection:
    websocket: WebSocket
    device_type: DeviceType
    last_active: float


class ConnectionManager:
    """Manages WebSocket connections partitioned by user_id and device_id."""

    def __init__(self) -> None:
        self._rooms: dict[str, dict[str, DeviceConnection]] = {}
        self._lock = asyncio.Lock()
        self._heartbeat_task: asyncio.Task | None = None

    async def connect(
        self,
        user_id: str,
        device_id: str,
        device_type: DeviceType,
        websocket: WebSocket,
    ) -> None:
        async with self._lock:
            if user_id not in self._rooms:
                self._rooms[user_id] = {}

            existing = self._rooms[user_id].get(device_id)
            if existing:
                try:
                    await existing.websocket.close(code=1000, reason="Replaced by new connection")
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"Error closing replaced socket: {exc}")

            self._rooms[user_id][device_id] = DeviceConnection(
                websocket=websocket,
                device_type=device_type,
                last_active=time.time(),
            )
            logger.info(
                f"Device connected: {device_id} ({device_type}) for user {user_id}",
                extra={"user_id": user_id, "device_id": device_id, "event_type": "ws_connect"},
            )

    async def disconnect(self, user_id: str, device_id: str) -> None:
        async with self._lock:
            if user_id in self._rooms and device_id in self._rooms[user_id]:
                del self._rooms[user_id][device_id]
                if not self._rooms[user_id]:
                    del self._rooms[user_id]
                logger.info(
                    f"Device disconnected: {device_id} for user {user_id}",
                    extra={"user_id": user_id, "device_id": device_id, "event_type": "ws_disconnect"},
                )

    def mark_alive(self, user_id: str, device_id: str) -> None:
        if user_id in self._rooms and device_id in self._rooms[user_id]:
            self._rooms[user_id][device_id].last_active = time.time()

    async def send_to_device(
        self, user_id: str, device_id: str, envelope: Envelope
    ) -> bool:
        conn: DeviceConnection | None = None
        async with self._lock:
            if user_id in self._rooms:
                conn = self._rooms[user_id].get(device_id)

        if not conn:
            return False

        try:
            await conn.websocket.send_text(envelope.model_dump_json())
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"Failed to send to device {device_id}: {exc}",
                extra={"user_id": user_id, "device_id": device_id, "event_type": "ws_send_fail"},
            )
            await self.disconnect(user_id, device_id)
            return False

    async def broadcast(
        self,
        user_id: str,
        envelope: Envelope,
        exclude: str | None = None,
    ) -> None:
        recipients: list[tuple[str, WebSocket]] = []
        async with self._lock:
            room = self._rooms.get(user_id, {})
            for dev_id, conn in room.items():
                if exclude and dev_id == exclude:
                    continue
                recipients.append((dev_id, conn.websocket))

        dead_devices: set[str] = set()
        text_data = envelope.model_dump_json()
        for dev_id, ws in recipients:
            try:
                await ws.send_text(text_data)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"Broadcast failed to {dev_id}: {exc}",
                    extra={"user_id": user_id, "device_id": dev_id, "event_type": "ws_broadcast_fail"},
                )
                dead_devices.add(dev_id)

        for dev_id in dead_devices:
            await self.disconnect(user_id, dev_id)

    def find_devices(
        self, user_id: str, device_type: DeviceType | None = None
    ) -> list[str]:
        room = self._rooms.get(user_id, {})
        if device_type is None:
            return list(room.keys())
        return [
            dev_id
            for dev_id, conn in room.items()
            if conn.device_type == device_type
        ]

    def online_count(self) -> int:
        return sum(len(devices) for devices in self._rooms.values())

    async def _heartbeat_worker(self) -> None:
        while True:
            try:
                await asyncio.sleep(20)
                now = time.time()
                to_disconnect: list[tuple[str, str]] = []
                pings: list[tuple[str, str, WebSocket]] = []

                async with self._lock:
                    for uid, devices in self._rooms.items():
                        for did, conn in devices.items():
                            if now - conn.last_active > 60:
                                to_disconnect.append((uid, did))
                            else:
                                pings.append((uid, did, conn.websocket))

                for uid, did in to_disconnect:
                    logger.info(
                        f"Heartbeat timeout for {did}",
                        extra={"user_id": uid, "device_id": did, "event_type": "ws_heartbeat_timeout"},
                    )
                    await self.disconnect(uid, did)

                ping_env = Envelope(
                    type=EnvelopeType.ping,
                    source_device_id="server",
                    ts=datetime.now(UTC).isoformat(),
                    payload={"server_time": now},
                )
                ping_str = ping_env.model_dump_json()

                for uid, did, ws in pings:
                    try:
                        await ws.send_text(ping_str)
                    except Exception:  # noqa: BLE001
                        await self.disconnect(uid, did)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Error in heartbeat loop: {exc}")

    def start_heartbeat(self) -> None:
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_worker())

    async def stop_heartbeat(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None


ws_manager = ConnectionManager()
