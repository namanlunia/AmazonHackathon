import json

from fastapi.testclient import TestClient


def test_ws_single_device_lifecycle(client: TestClient) -> None:
    user_id = "test-user-ws1"
    dev_id = "firefox-1"

    with client.websocket_connect(f"/ws/{user_id}/{dev_id}?device_type=firefox") as ws:
        # 1. Expect initial snapshot
        snapshot_raw = ws.receive_text()
        snap = json.loads(snapshot_raw)
        assert snap["type"] == "snapshot"
        assert snap["payload"]["user_id"] == user_id

        # 2. Ping-pong
        ws.send_text(
            json.dumps(
                {
                    "type": "ping",
                    "source_device_id": dev_id,
                    "payload": {"nonce": 123},
                }
            )
        )
        pong = json.loads(ws.receive_text())
        assert pong["type"] == "pong"
        assert pong["payload"]["nonce"] == 123

        # 3. Publish state_update
        ws.send_text(
            json.dumps(
                {
                    "type": "state_update",
                    "source_device_id": dev_id,
                    "payload": {
                        "device_type": "firefox",
                        "tabs": [
                            {
                                "title": "AWS re:Invent Keynote",
                                "url": "https://aws.amazon.com/reinvent",
                                "active": True,
                                "media_position_seconds": 120.5,
                            }
                        ],
                    },
                }
            )
        )
        ack = json.loads(ws.receive_text())
        assert ack["type"] == "ack"

        # 4. Verify REST state reflects update and active_context
        res = client.get(f"/api/v1/state/{user_id}")
        assert res.status_code == 200
        state_data = res.json()
        assert dev_id in state_data["devices"]
        assert state_data["active_context"]["title"] == "AWS re:Invent Keynote"
        assert state_data["active_context"]["position_seconds"] == 120.5


def test_ws_mesh_broadcast_and_disconnect(client: TestClient) -> None:
    user_id = "test-user-ws2"

    with client.websocket_connect(f"/ws/{user_id}/dev1?device_type=firefox") as ws1:
        snap1 = json.loads(ws1.receive_text())
        assert snap1["type"] == "snapshot"

        with client.websocket_connect(f"/ws/{user_id}/dev2?device_type=fire_tv") as ws2:
            snap2 = json.loads(ws2.receive_text())
            assert snap2["type"] == "snapshot"

            # ws1 should receive presence announcement for dev2
            presence2 = json.loads(ws1.receive_text())
            assert presence2["type"] == "presence"
            assert presence2["payload"]["device_id"] == "dev2"
            assert presence2["payload"]["online"] is True

            # dev2 publishes state update
            ws2.send_text(
                json.dumps(
                    {
                        "type": "state_update",
                        "source_device_id": "dev2",
                        "payload": {
                            "device_type": "fire_tv",
                            "now_playing": {
                                "title": "Lord of the Rings",
                                "app": "Prime Video",
                                "url": "https://amazon.com/lotr",
                                "position_seconds": 300.0,
                                "duration_seconds": 10000.0,
                                "state": "playing",
                            },
                        },
                    }
                )
            )
            ack2 = json.loads(ws2.receive_text())
            assert ack2["type"] == "ack"

            # ws1 should receive broadcast of state_update
            bcast = json.loads(ws1.receive_text())
            assert bcast["type"] == "state_update"
            assert bcast["payload"]["now_playing"]["title"] == "Lord of the Rings"

        # ws2 disconnected: ws1 should receive presence offline
        offline_msg = json.loads(ws1.receive_text())
        assert offline_msg["type"] == "presence"
        assert offline_msg["payload"]["device_id"] == "dev2"
        assert offline_msg["payload"]["online"] is False


def test_ws_error_handling_and_rest_command(client: TestClient) -> None:
    user_id = "test-user-ws3"
    dev_id = "target-dev"

    with client.websocket_connect(f"/ws/{user_id}/{dev_id}?device_type=smart_home") as ws:
        _ = ws.receive_text()  # snapshot

        # Send invalid JSON
        ws.send_text("not-a-valid-json")
        err = json.loads(ws.receive_text())
        assert err["type"] == "error"

        # Socket still works after error: test REST command delivery
        cmd_res = client.post(
            f"/api/v1/devices/{user_id}/{dev_id}/command",
            json={"action": "power_off"},
        )
        assert cmd_res.status_code == 200

        cmd_env = json.loads(ws.receive_text())
        assert cmd_env["type"] == "command"
        assert cmd_env["payload"]["action"] == "power_off"

    # Now that target-dev is disconnected, command returns 404
    cmd_res_offline = client.post(
        f"/api/v1/devices/{user_id}/{dev_id}/command",
        json={"action": "power_off"},
    )
    assert cmd_res_offline.status_code == 404
