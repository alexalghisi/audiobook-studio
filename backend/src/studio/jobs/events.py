"""Project event stream over Redis pub/sub.

Workers and the API publish segment status changes and job progress here;
each open project page holds one SSE connection that relays them.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from redis.asyncio import Redis
from redis.asyncio.client import PubSub


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

    @asynccontextmanager
    async def subscription(self, project_id: uuid.UUID | str) -> AsyncIterator[Subscription]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self.channel(project_id))
        try:
            yield Subscription(pubsub)
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()  # type: ignore[no-untyped-call]


class Subscription:
    def __init__(self, pubsub: PubSub) -> None:
        self._pubsub = pubsub

    async def get(self, wait_seconds: float) -> dict[str, Any] | None:
        """The next event, or None if nothing arrived within `wait_seconds`."""
        message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=wait_seconds)
        if message is None:
            return None
        data = message["data"]
        event: dict[str, Any] = json.loads(data.decode() if isinstance(data, bytes) else data)
        return event
