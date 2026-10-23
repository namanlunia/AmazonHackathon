import argparse
import asyncio
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import websockets


class MeshDevice:
    """Simulates a connected client device over WebSocket."""

    def __init__(self, device_id: str, device_type: str) -> None:
        self.device_id = device_id
        self.device_type = device_type
        self.ws: websockets.WebSocketClientProtocol | None = None
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None

    async def connect(self, ws_base_url: str, user_id: str, api_key: str) -> None:
        query = f"device_type={self.device_type}"
        if api_key:
            query += f"&api_key={api_key}"
        url = f"{ws_base_url}/ws/{user_id}/{self.device_id}?{query}"
        self.ws = await websockets.connect(url)
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def _reader_loop(self) -> None:
        try:
            assert self.ws is not None
            async for raw in self.ws:
                data = json.loads(raw)
                await self.queue.put(data)
        except Exception:  # noqa: BLE001, S110
            pass

    async def send(self, envelope: dict[str, Any]) -> None:
        assert self.ws is not None
        await self.ws.send(json.dumps(envelope))

    async def wait_for(
        self,
        envelope_type: str,
        timeout: float = 5.0,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(0.05, deadline - time.time())
            try:
                msg = await asyncio.wait_for(self.queue.get(), timeout=remaining)
                if msg.get("type") == envelope_type and (predicate is None or predicate(msg)):
                    return msg
            except TimeoutError:
                break
        raise TimeoutError(
            f"Device {self.device_id} timed out waiting for envelope '{envelope_type}'"
        )

    async def disconnect(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self.ws:
            await self.ws.close()


def print_log(step_num: int, message: str, latency_ms: float, status: str = "PASSED") -> None:
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    print(f"[{now_iso}] Step {step_num}: {message} ({latency_ms:.2f}ms) - {status}")


async def run_simulation(base_url: str, api_key: str, user_id: str) -> None:
    ws_base = base_url.replace("http://", "ws://").replace("https://", "wss://")
    http_headers = {"Content-Type": "application/json", "X-User-Id": user_id}
    if api_key:
        http_headers["X-API-Key"] = api_key

    firefox = MeshDevice("firefox-mac", "firefox")
    fire_tv = MeshDevice("firetv-livingroom", "fire_tv")
    smart_light = MeshDevice("light-livingroom", "smart_home")

    async with httpx.AsyncClient(base_url=base_url, headers=http_headers, timeout=10.0) as http:
        # Step 1: Connect all three devices and verify snapshot reception
        t0 = time.perf_counter()
        await firefox.connect(ws_base, user_id, api_key)
        await fire_tv.connect(ws_base, user_id, api_key)
        await smart_light.connect(ws_base, user_id, api_key)

        ff_snap = await firefox.wait_for("snapshot")
        tv_snap = await fire_tv.wait_for("snapshot")
        light_snap = await smart_light.wait_for("snapshot")

        assert ff_snap["type"] == "snapshot", "Firefox snapshot missing"
        assert tv_snap["type"] == "snapshot", "Fire TV snapshot missing"
        assert light_snap["type"] == "snapshot", "Smart Light snapshot missing"

        # Initialize Smart Light metadata so it can be controlled by Alexa in step 6
        await smart_light.send({
            "type": "state_update",
            "source_device_id": smart_light.device_id,
            "payload": {
                "device_type": "smart_home",
                "name": "Living Room Light",
                "power": "on",
                "attributes": {"brightness": 100},
            },
        })
        await smart_light.wait_for("ack")
        print_log(1, "All 3 devices connected and received initial snapshots", (time.perf_counter() - t0) * 1000)

        # Step 2: Firefox sends state_update with active video tab at 754s
        t0 = time.perf_counter()
        tab_url = "https://video.amazon.com/watch/prime-continuity"
        tab_title = "The Expanse S1E1"
        await firefox.send({
            "type": "state_update",
            "source_device_id": firefox.device_id,
            "payload": {
                "device_type": "firefox",
                "tabs": [
                    {
                        "title": tab_title,
                        "url": tab_url,
                        "active": True,
                        "media_position_seconds": 754.0,
                    }
                ],
            },
        })
        await firefox.wait_for("ack")
        print_log(2, "Firefox published active video tab at 754s", (time.perf_counter() - t0) * 1000)

        # Step 3: Alexa ContinueOnFireTVIntent -> Fire TV receives command{play, 754s}
        t0 = time.perf_counter()
        alexa_payload_step3 = {
            "version": "1.0",
            "request": {
                "type": "IntentRequest",
                "intent": {"name": "ContinueOnFireTVIntent", "slots": {}},
            },
        }
        res3 = await http.post("/alexa", json=alexa_payload_step3)
        assert res3.status_code == 200, f"Alexa error {res3.status_code}: {res3.text}"
        alexa_json3 = res3.json()
        speech3 = alexa_json3["response"]["outputSpeech"]["text"]
        assert "Fire TV" in speech3, f"Speech did not mention Fire TV: '{speech3}'"

        tv_cmd = await fire_tv.wait_for(
            "command",
            predicate=lambda m: m.get("payload", {}).get("action") == "play",
        )
        assert tv_cmd["payload"]["position_seconds"] == 754.0, "Position seconds mismatch"
        assert tv_cmd["payload"]["url"] == tab_url, "URL mismatch"
        print_log(3, f"Alexa ContinueOnFireTVIntent delivered play command ({speech3})", (time.perf_counter() - t0) * 1000)

        # Step 4: Fire TV sends now_playing -> Firefox receives broadcast
        t0 = time.perf_counter()
        await fire_tv.send({
            "type": "state_update",
            "source_device_id": fire_tv.device_id,
            "payload": {
                "device_type": "fire_tv",
                "now_playing": {
                    "title": tab_title,
                    "app": "Prime Video",
                    "url": tab_url,
                    "position_seconds": 754.0,
                    "duration_seconds": 3600.0,
                    "state": "playing",
                },
            },
        })
        ff_broadcast = await firefox.wait_for(
            "state_update",
            predicate=lambda m: m.get("source_device_id") == fire_tv.device_id,
        )
        assert ff_broadcast["payload"]["now_playing"]["state"] == "playing"
        print_log(4, "Fire TV published now_playing, broadcast received by Firefox", (time.perf_counter() - t0) * 1000)

        # Step 5: Alexa AskContextIntent "what's open on my browser" -> speech contains tab title
        t0 = time.perf_counter()
        alexa_payload_step5 = {
            "version": "1.0",
            "request": {
                "type": "IntentRequest",
                "intent": {
                    "name": "AskContextIntent",
                    "slots": {
                        "query": {
                            "name": "query",
                            "value": "what's open on my browser",
                        }
                    },
                },
            },
        }
        res5 = await http.post("/alexa", json=alexa_payload_step5)
        assert res5.status_code == 200, f"Alexa error {res5.status_code}: {res5.text}"
        speech5 = res5.json()["response"]["outputSpeech"]["text"]
        assert tab_title in speech5, f"Expected tab title '{tab_title}' in speech: '{speech5}'"
        print_log(5, f"Alexa AskContextIntent responded with browser tab: '{speech5}'", (time.perf_counter() - t0) * 1000)

        # Step 6: Alexa SmartHomeIntent "turn off living room light" -> socket receives power_off
        t0 = time.perf_counter()
        alexa_payload_step6 = {
            "version": "1.0",
            "request": {
                "type": "IntentRequest",
                "intent": {
                    "name": "SmartHomeIntent",
                    "slots": {
                        "device": {"name": "device", "value": "living room light"},
                        "action": {"name": "action", "value": "turn off"},
                    },
                },
            },
        }
        res6 = await http.post("/alexa", json=alexa_payload_step6)
        assert res6.status_code == 200, f"Alexa error {res6.status_code}: {res6.text}"
        speech6 = res6.json()["response"]["outputSpeech"]["text"]
        assert "off" in speech6.lower(), f"Expected 'off' in speech: '{speech6}'"

        light_cmd = await smart_light.wait_for(
            "command",
            predicate=lambda m: m.get("payload", {}).get("action") == "power_off",
        )
        assert light_cmd["payload"]["action"] == "power_off"
        print_log(6, f"Smart light received power_off command via Alexa voice action ({speech6})", (time.perf_counter() - t0) * 1000)

        # Step 7: Fire TV disconnects -> others receive presence offline; Alexa graceful fallback
        t0 = time.perf_counter()
        await fire_tv.disconnect()

        ff_presence = await firefox.wait_for(
            "presence",
            predicate=lambda m: m.get("payload", {}).get("device_id") == fire_tv.device_id
            and m.get("payload", {}).get("online") is False,
        )
        assert ff_presence["payload"]["online"] is False

        res7 = await http.post("/alexa", json=alexa_payload_step3)
        assert res7.status_code == 200
        speech7 = res7.json()["response"]["outputSpeech"]["text"]
        assert "offline" in speech7.lower(), f"Expected offline message, got: '{speech7}'"
        print_log(7, f"Fire TV offline presence verified and Alexa responded gracefully ({speech7})", (time.perf_counter() - t0) * 1000)

    await firefox.disconnect()
    await smart_light.disconnect()
    print("\nAll 7 mesh scenario steps completed successfully.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate cross-device mesh interactions.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Hub base URL")
    parser.add_argument("--api-key", default="", help="Hub API key if auth is enabled")
    parser.add_argument("--user-id", default="demo-user", help="User context ID")
    args = parser.parse_args()

    try:
        asyncio.run(run_simulation(args.base_url, args.api_key, args.user_id))
    except Exception as exc:  # noqa: BLE001
        print(f"\nSimulation FAILED: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
