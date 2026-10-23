import json

from fastapi.testclient import TestClient


def test_ws_payload_validation(client: TestClient) -> None:
    user_id = "test-payload-val-user"
    dev_id = "ff-1"

    with client.websocket_connect(f"/ws/{user_id}/{dev_id}?device_type=firefox") as ws:
        _ = ws.receive_text()  # snapshot

        # 1. Invalid payload structure: tabs should be a list, pass an int
        ws.send_text(
            json.dumps(
                {
                    "type": "state_update",
                    "source_device_id": dev_id,
                    "payload": {
                        "device_type": "firefox",
                        "tabs": 12345,
                    },
                }
            )
        )
        err_msg = json.loads(ws.receive_text())
        assert err_msg["type"] == "error"
        assert "Invalid payload structure" in err_msg["payload"]["error"]

        # Socket is still alive: test ping/pong
        ws.send_text(json.dumps({"type": "ping", "source_device_id": dev_id, "payload": {}}))
        pong = json.loads(ws.receive_text())
        assert pong["type"] == "pong"

        # 2. Mismatched device_type: connected as firefox, sending fire_tv
        ws.send_text(
            json.dumps(
                {
                    "type": "state_update",
                    "source_device_id": dev_id,
                    "payload": {
                        "device_type": "fire_tv",
                        "now_playing": None,
                    },
                }
            )
        )
        err_msg2 = json.loads(ws.receive_text())
        assert err_msg2["type"] == "error"
        assert "Mismatched device_type" in err_msg2["payload"]["error"]


def test_rest_payload_validation(client: TestClient) -> None:
    user_id = "test-rest-payload-user"

    # 1. Invalid payload structure -> 422
    res_bad = client.put(
        f"/api/v1/devices/{user_id}/tv-1/state?device_type=fire_tv",
        json={"now_playing": "not-a-dict"},
    )
    assert res_bad.status_code == 422

    # 2. Mismatched device_type -> 422
    res_mismatch = client.put(
        f"/api/v1/devices/{user_id}/tv-1/state?device_type=fire_tv",
        json={"device_type": "firefox", "tabs": []},
    )
    assert res_mismatch.status_code == 422

    # 3. Valid payload -> 200
    res_valid = client.put(
        f"/api/v1/devices/{user_id}/tv-1/state?device_type=fire_tv",
        json={
            "device_type": "fire_tv",
            "now_playing": {
                "title": "Valid Stream",
                "app": "Prime Video",
                "position_seconds": 10.0,
                "duration_seconds": 100.0,
                "state": "playing",
            },
        },
    )
    assert res_valid.status_code == 200
