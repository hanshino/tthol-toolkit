import asyncio

from fastapi.testclient import TestClient

from services.api import build_app
from services.api_types import WorldSnapshot


def test_world_ws_receives_frame():
    app = build_app(services=None)
    stream = app.state.services["world_stream"]  # set up by build_app when services=None

    async def push():
        await asyncio.sleep(0.05)
        await stream.publish(WorldSnapshot(chars=[], server_ts=42.0))

    client = TestClient(app)
    with client.websocket_connect("/ws/world") as ws:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(push())
        msg = ws.receive_json()
        assert msg["server_ts"] == 42.0
