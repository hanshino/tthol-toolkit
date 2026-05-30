"""In-process pub/sub for live WorldSnapshot frames feeding /ws/world.

Bounded per-subscriber queues with drop-oldest backpressure. Snapshots
are idempotent - only the latest frame matters, so dropping older
frames during slowdowns is correct.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

from services.api_types import WorldSnapshot


class WorldStream:
    def __init__(self, maxsize: int = 4) -> None:
        self._maxsize = maxsize
        self._subscribers: list[asyncio.Queue[WorldSnapshot]] = []
        self._lock = asyncio.Lock()

    def subscribe(self) -> asyncio.Queue[WorldSnapshot]:
        q: asyncio.Queue[WorldSnapshot] = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[WorldSnapshot]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def publish(self, snap: WorldSnapshot) -> None:
        async with self._lock:
            for q in list(self._subscribers):
                while q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                q.put_nowait(snap)

    def __iter__(self) -> Iterator[asyncio.Queue[WorldSnapshot]]:
        return iter(self._subscribers)
