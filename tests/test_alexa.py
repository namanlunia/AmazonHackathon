import json

from fastapi.testclient import TestClient

from app.config import get_settings


def test_alexa_launch_and_system_intents(client: TestClient) -> None:
    # 1. LaunchRequest
    launch_res = client.post("/alexa", json={"request": {"type": "LaunchRequest"}})
    assert launch_res.status_code == 200
    data = launch_res.json()
    assert data["response"]["shouldEndSession"] is False
    assert "Continuity Hub" in data["response"]["outputSpeech"]["text"]
    assert data["response"]["reprompt"] is not None

    # 2. AMAZON.HelpIntent
    help_res = client.post("/alexa", json={
        "request": {
            "type": "IntentRequest",
            "intent": {"name": "AMAZON.HelpIntent"},
        }
    })
    assert help_res.status_code == 200
    assert help_res.json()["response"]["shouldEndSession"] is False

    # 3. AMAZON.StopIntent
    stop_res = client.post("/alexa", json={
        "request": {
            "type": "IntentRequest",
            "intent": {"name": "AMAZON.StopIntent"},
        }
    })
    assert stop_res.status_code == 200
    assert stop_res.json()["response"]["shouldEndSession"] is True
    assert "Goodbye" in stop_res.json()["response"]["outputSpeech"]["text"]


def test_alexa_whats_open_and_active_context(client: TestClient) -> None:
    user_id = "test-alexa-user"
    headers = {"X-User-Id": user_id}

    # Nothing open initially
    res_empty = client.post(
        "/alexa",
        headers=headers,
        json={
            "request": {
                "type": "IntentRequest",
                "intent": {
                    "name": "WhatsOpenIntent",
                    "slots": {"query": {"name": "query", "value": "what is open"}},
                },
            }
        },
    )
    assert res_empty.status_code == 200
    assert "no open tabs" in res_empty.json()["response"]["outputSpeech"]["text"].lower()

    # Update state via REST
    put_res = client.put(
        f"/api/v1/devices/{user_id}/firefox-1/state?device_type=firefox",
        headers=headers,
        json={
            "tabs": [
                {
                    "title": "AWS re:Invent Keynote",
                    "url": "https://aws.amazon.com/reinvent",
                    "active": True,
                    "media_position_seconds": 600.0,
                }
            ]
        },
    )
    assert put_res.status_code == 200

    # Query again
    res_open = client.post(
        "/alexa",
        headers=headers,
        json={
            "request": {
                "type": "IntentRequest",
                "intent": {
                    "name": "AskContextIntent",
                    "slots": {"query": {"name": "query", "value": "what's open on my browser"}},
                },
            }
        },
    )
    assert res_open.status_code == 200
    speech = res_open.json()["response"]["outputSpeech"]["text"]
    assert "AWS re:Invent Keynote" in speech


def test_alexa_continue_on_fire_tv_flow(client: TestClient) -> None:
    user_id = "test-continuity-user"
    headers = {"X-User-Id": user_id}

    # 1. No active context
    res_none = client.post(
        "/alexa",
        headers=headers,
        json={
            "request": {
                "type": "IntentRequest",
                "intent": {"name": "ContinueOnFireTVIntent"},
            }
        },
    )
    assert "no active video" in res_none.json()["response"]["outputSpeech"]["text"].lower()

    # 2. Add video in Firefox but Fire TV is offline
    client.put(
        f"/api/v1/devices/{user_id}/firefox-1/state?device_type=firefox",
        headers=headers,
        json={
            "tabs": [
                {
                    "title": "Documentary Stream",
                    "url": "https://video.example.com/stream",
                    "active": True,
                    "media_position_seconds": 450.0,
                }
            ]
        },
    )
    res_offline = client.post(
        "/alexa",
        headers=headers,
        json={
            "request": {
                "type": "IntentRequest",
                "intent": {"name": "ContinueOnFireTVIntent"},
            }
        },
    )
    assert "offline" in res_offline.json()["response"]["outputSpeech"]["text"].lower()

    # 3. Connect Fire TV and continue
    with client.websocket_connect(f"/ws/{user_id}/firetv-1?device_type=fire_tv") as tv_ws:
        _ = tv_ws.receive_text()  # snapshot

        res_continue = client.post(
            "/alexa",
            headers=headers,
            json={
                "request": {
                    "type": "IntentRequest",
                    "intent": {"name": "ContinueOnFireTVIntent"},
                }
            },
        )
        assert res_continue.status_code == 200
        speech = res_continue.json()["response"]["outputSpeech"]["text"]
        assert "Fire TV" in speech
        assert "450" in speech

        cmd_raw = tv_ws.receive_text()
        cmd = json.loads(cmd_raw)
        assert cmd["type"] == "command"
        assert cmd["payload"]["action"] == "play"
        assert cmd["payload"]["position_seconds"] == 450.0


def test_alexa_smart_home_control(client: TestClient) -> None:
    user_id = "test-smarthome-user"
    headers = {"X-User-Id": user_id}

    with client.websocket_connect(f"/ws/{user_id}/light-1?device_type=smart_home") as light_ws:
        _ = light_ws.receive_text()

        # Update initial light metadata
        light_ws.send_text(json.dumps({
            "type": "state_update",
            "source_device_id": "light-1",
            "payload": {
                "device_type": "smart_home",
                "name": "Porch Light",
                "power": "on",
                "attributes": {},
            },
        }))
        _ = light_ws.receive_text()  # ack

        # Voice command to turn off
        res = client.post(
            "/alexa",
            headers=headers,
            json={
                "request": {
                    "type": "IntentRequest",
                    "intent": {
                        "name": "SmartHomeIntent",
                        "slots": {
                            "device": {"name": "device", "value": "porch light"},
                            "action": {"name": "action", "value": "turn off"},
                        },
                    },
                }
            },
        )
        assert res.status_code == 200
        speech = res.json()["response"]["outputSpeech"]["text"]
        assert "Turned off Porch Light" in speech

        cmd = json.loads(light_ws.receive_text())
        assert cmd["type"] == "command"
        assert cmd["payload"]["action"] == "power_off"


def test_alexa_auth_and_resilience(client: TestClient) -> None:
    settings = get_settings()
    settings.HUB_API_KEY = "secret-token-123"

    try:
        # Unauthorized without header
        res_unauth = client.post("/alexa", json={"request": {"type": "LaunchRequest"}})
        assert res_unauth.status_code == 401

        # Authorized with header
        res_auth = client.post(
            "/alexa",
            headers={"X-API-Key": "secret-token-123"},
            json={"request": {"type": "LaunchRequest"}},
        )
        assert res_auth.status_code == 200

        # Resilience: malformed internal body still returns 200 with polite apology
        res_bad = client.post(
            "/alexa",
            headers={"X-API-Key": "secret-token-123"},
            json={"invalid_root": True},
        )
        assert res_bad.status_code == 200
        assert "could not understand" in res_bad.json()["response"]["outputSpeech"]["text"].lower()
    finally:
        settings.HUB_API_KEY = ""
