import json

from fastapi.testclient import TestClient


def test_same_device_reconnect_race(client: TestClient) -> None:
    user_id = "test-reconnect-user"
    dev_id = "firefox-mac"
    peer_id = "listener-dev"

    # Connect a peer device to monitor presence broadcasts
    with client.websocket_connect(f"/ws/{user_id}/{peer_id}?device_type=smart_home") as peer_ws:
        _ = peer_ws.receive_text()  # initial snapshot for peer

        ws1_ctx = client.websocket_connect(f"/ws/{user_id}/{dev_id}?device_type=firefox")
        ws1 = ws1_ctx.__enter__()
        _ = ws1.receive_text()  # snapshot for ws1
        _ = peer_ws.receive_text()  # presence online for ws1

        ws2_ctx = client.websocket_connect(f"/ws/{user_id}/{dev_id}?device_type=firefox")
        ws2 = ws2_ctx.__enter__()
        _ = ws2.receive_text()  # snapshot for ws2
        _ = peer_ws.receive_text()  # presence online for ws2

        # Verify connection count in /health is 2 (listener-dev + active firefox-mac)
        health_res = client.get("/health")
        assert health_res.status_code == 200
        assert health_res.json()["connections"] == 2

        # REST command delivery reaches ws2
        cmd_res = client.post(
            f"/api/v1/devices/{user_id}/{dev_id}/command",
            json={"action": "open_url", "url": "https://example.com/stream"},
        )
        assert cmd_res.status_code == 200

        cmd_msg = json.loads(ws2.receive_text())
        assert cmd_msg["type"] == "command"
        assert cmd_msg["payload"]["action"] == "open_url"

        # Close ws1 (simulating old connection termination)
        ws1_ctx.__exit__(None, None, None)

        # Confirm closing the FIRST socket does NOT mark the device offline
        state_res = client.get(f"/api/v1/state/{user_id}")
        assert state_res.status_code == 200
        assert state_res.json()["devices"][dev_id]["online"] is True

        health_res2 = client.get("/health")
        assert health_res2.json()["connections"] == 2

        # A new REST command still reaches ws2
        cmd_res2 = client.post(
            f"/api/v1/devices/{user_id}/{dev_id}/command",
            json={"action": "play"},
        )
        assert cmd_res2.status_code == 200
        cmd_msg2 = json.loads(ws2.receive_text())
        assert cmd_msg2["payload"]["action"] == "play"

        # Now close ws2 (active connection terminates)
        ws2_ctx.__exit__(None, None, None)

        # Closing ws2 MUST unmark the device in state store and broadcast presence offline
        state_res3 = client.get(f"/api/v1/state/{user_id}")
        assert state_res3.json()["devices"][dev_id]["online"] is False

        offline_presence = json.loads(peer_ws.receive_text())
        assert offline_presence["type"] == "presence"
        assert offline_presence["payload"]["device_id"] == dev_id
        assert offline_presence["payload"]["online"] is False
