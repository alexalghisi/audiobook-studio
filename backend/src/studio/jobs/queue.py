"""A small reliable job queue on Redis.

PostgreSQL holds the job record and is the source of truth for its status.
Redis only answers "which job should a worker pick up next", with the classic
reliable-queue pattern:

- `ready` is a FIFO list. A worker claims with BLMOVE into its own
  `processing:<worker>` list, so a claimed job is never only in memory.
- A worker holds a `lease:<job>` (SET NX with a TTL) while it runs a job and
  refreshes its `worker:<id>` liveness key. A job delivered twice is skipped
  by whoever fails to get the lease.
- Any worker periodically moves jobs out of the processing lists of workers
  whose liveness key has expired. That is how a job survives `kill -9`.
- Retries wait in the `delayed` sorted set until they are due.
"""

from __future__ import annotations

import time
from typing import Any, cast

from redis.asyncio import Redis

# Moves every due member of the delayed set to the ready list atomically.
_PROMOTE_DUE = """
local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, 100)
for _, id in ipairs(due) do
  redis.call('ZREM', KEYS[1], id)
  redis.call('LPUSH', KEYS[2], id)
end
return #due
"""


class JobQueue:
    def __init__(self, redis: Redis, prefix: str = "studio", lease_seconds: float = 15.0) -> None:
        self._redis = redis
        self._p = f"{prefix}:q"
        self.lease_ms = int(lease_seconds * 1000)
        self._promote = redis.register_script(_PROMOTE_DUE)

    @property
    def ready_key(self) -> str:
        return f"{self._p}:ready"

    @property
    def delayed_key(self) -> str:
        return f"{self._p}:delayed"

    def processing_key(self, worker_id: str) -> str:
        return f"{self._p}:processing:{worker_id}"

    def _worker_key(self, worker_id: str) -> str:
        return f"{self._p}:worker:{worker_id}"

    def _lease_key(self, job_id: str) -> str:
        return f"{self._p}:lease:{job_id}"

    def _cancel_key(self, job_id: str) -> str:
        return f"{self._p}:cancel:{job_id}"

    async def _call(self, result: Any) -> Any:
        # redis-py types every command as `Awaitable[T] | T`; on the asyncio client it is always awaitable.
        return await result

    async def enqueue(self, job_id: str) -> None:
        await self._call(self._redis.lpush(self.ready_key, job_id))

    async def enqueue_at(self, job_id: str, due: float) -> None:
        await self._redis.zadd(self.delayed_key, {job_id: due})

    async def promote_due(self, now: float | None = None) -> int:
        moved = await self._promote(keys=[self.delayed_key, self.ready_key], args=[now or time.time()])
        return int(moved)

    async def claim(self, worker_id: str, wait_seconds: float) -> str | None:
        raw = await self._call(
            self._redis.blmove(self.ready_key, self.processing_key(worker_id), wait_seconds, "RIGHT", "LEFT")  # type: ignore[arg-type]
        )
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    async def acquire_lease(self, worker_id: str, job_id: str) -> bool:
        return bool(await self._redis.set(self._lease_key(job_id), worker_id, nx=True, px=self.lease_ms))

    async def heartbeat(self, worker_id: str, job_ids: list[str]) -> None:
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.set(self._worker_key(worker_id), "1", px=self.lease_ms)
            for job_id in job_ids:
                pipe.pexpire(self._lease_key(job_id), self.lease_ms)
            await pipe.execute()

    async def ack(self, worker_id: str, job_id: str) -> None:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.lrem(self.processing_key(worker_id), 0, job_id)
            pipe.delete(self._lease_key(job_id))
            await pipe.execute()

    async def drop(self, worker_id: str, job_id: str) -> None:
        """Removes a duplicate delivery without touching the lease its owner holds."""
        await self._call(self._redis.lrem(self.processing_key(worker_id), 0, job_id))

    async def release_worker(self, worker_id: str) -> None:
        await self._redis.delete(self._worker_key(worker_id))

    async def requeue_orphans(self) -> list[str]:
        """Returns jobs left in processing lists of workers that are no longer alive."""
        recovered: list[str] = []
        pattern = f"{self._p}:processing:*"
        async for raw_key in self._redis.scan_iter(match=pattern, count=100):
            key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
            worker_id = key.rsplit(":", 1)[-1]
            if await self._redis.exists(self._worker_key(worker_id)):
                continue
            while True:
                # LMOVE is atomic, so two workers reaping at once never duplicate a job.
                raw = await self._call(self._redis.lmove(key, self.ready_key, "RIGHT", "LEFT"))
                if raw is None:
                    break
                recovered.append(raw.decode() if isinstance(raw, bytes) else str(raw))
        return recovered

    async def is_known(self, job_id: str) -> bool:
        """Whether the job is anywhere in the queue (ready, delayed or being processed)."""
        if await self._call(self._redis.lpos(self.ready_key, job_id)) is not None:
            return True
        if await self._redis.zscore(self.delayed_key, job_id) is not None:
            return True
        return bool(await self._redis.exists(self._lease_key(job_id)))

    async def request_cancel(self, job_id: str) -> None:
        await self._redis.set(self._cancel_key(job_id), "1", ex=24 * 3600)

    async def cancel_requested(self, job_id: str) -> bool:
        return bool(await self._redis.exists(self._cancel_key(job_id)))

    async def depth(self) -> dict[str, int]:
        ready = cast(int, await self._call(self._redis.llen(self.ready_key)))
        delayed = await self._redis.zcard(self.delayed_key)
        return {"ready": ready, "delayed": delayed}
