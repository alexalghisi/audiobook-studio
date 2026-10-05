"""Project event stream over Redis pub/sub.

Workers and the API publish segment status changes and job progress here;
each open project page holds one SSE connection that relays them.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis


class EventBus:
    def __init__(self, redis: Redis, prefix: str = "studio") -> None:
        self._redis = redis
        self._prefix = prefix

    def channel(self, project_id: uuid.UUID | str) -> str:
        return f"{self._prefix}:events:{project_id}"

    async def publish(self, project_id: uuid.UUID | str, event: dict[str, Any]) -> None:
        await self._redis.publish(self.channel(project_id), json.dumps(event, default=str))

    async def publish_many(self, project_id: uuid.UUID | str, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        async with self._redis.pipeline(transaction=False) as pipe:
            for event in events:
                pipe.publish(self.channel(project_id), json.dumps(event, default=str))
            await pipe.execute()

    async def subscribe(
        self, project_id: uuid.UUID | str, poll_seconds: float = 1.0
    ) -> AsyncIterator[dict[str, Any] | None]:
        """Yields events, or None every `poll_seconds` without one (so callers can send keep-alives)."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self.channel(project_id))
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=poll_seconds)
                if message is None:
                    yield None
                    continue
                data = message["data"]
                yield json.loads(data.decode() if isinstance(data, bytes) else data)
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()  # type: ignore[no-untyped-call]
