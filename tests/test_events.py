import asyncio

from services.api_types import WorldSnapshot
from services.events import WorldStream


def _empty_snap() -> WorldSnapshot:
    return WorldSnapshot(chars=[], server_ts=0.0)


async def test_subscribe_receives_published_frame():
    stream = WorldStream()
    sub = stream.subscribe()
    await stream.publish(_empty_snap())
    snap = await asyncio.wait_for(sub.get(), timeout=0.5)
    assert snap.server_ts == 0.0


async def test_slow_subscriber_drops_oldest():
    stream = WorldStream(maxsize=2)
    sub = stream.subscribe()
    for i in range(5):
        snap = _empty_snap()
        snap.server_ts = float(i)
        await stream.publish(snap)
    # subscriber lagged - only the latest 2 frames should remain
    received = []
    while True:
        try:
            received.append(await asyncio.wait_for(sub.get(), timeout=0.1))
        except asyncio.TimeoutError:
            break
    assert len(received) == 2
    assert received[-1].server_ts == 4.0
