"""In-process pub/sub for live frames feeding /ws/world and /ws/pos.

Bounded per-subscriber queues with drop-oldest backpressure. Snapshots
are idempotent - only the latest frame matters, so dropping older
frames during slowdowns is correct.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Generic, TypeVar

from services.api_types import PositionFrame, WorldSnapshot

T = TypeVar("T")


class FrameStream(Generic[T]):
    def __init__(self, maxsize: int = 4) -> None:
        self._maxsize = maxsize
        self._subscribers: list[asyncio.Queue[T]] = []
        self._lock = asyncio.Lock()

    def subscribe(self) -> asyncio.Queue[T]:
        q: asyncio.Queue[T] = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[T]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def publish(self, snap: T) -> None:
        async with self._lock:
            for q in list(self._subscribers):
                while q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                q.put_nowait(snap)

    def __iter__(self) -> Iterator[asyncio.Queue[T]]:
        return iter(self._subscribers)


class WorldStream(FrameStream[WorldSnapshot]):
    pass


class PositionStream(FrameStream[PositionFrame]):
    """Position frames are deltas, so a dropped frame can lose a move; the
    queue is deeper than the world one to ride out a slow consumer."""

    def __init__(self, maxsize: int = 32) -> None:
        super().__init__(maxsize)
