from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from studio.jobs.context import JobContext, JobFailedError
from studio.services.synthesis import SynthesisStats, Synthesizer

Handler = Callable[[JobContext], Awaitable[dict[str, Any]]]


def scope_segment_ids(ctx: JobContext) -> list[uuid.UUID] | None:
    ids = ctx.scope.get("segment_ids")
    return [uuid.UUID(str(i)) for i in ids] if ids else None


def raise_on_failures(stats: SynthesisStats) -> None:
    if stats.failed:
        raise JobFailedError(
            f"{stats.failed} of {stats.segments} segments failed to synthesize",
            # Retrying helps if at least one failure was transient; cached segments are free.
            retryable=stats.retryable_failures > 0,
        )


def synthesize_handler(synthesizer: Synthesizer) -> Handler:
    async def handle(ctx: JobContext) -> dict[str, Any]:
        stats = await synthesizer.run(ctx, scope_segment_ids(ctx))
        raise_on_failures(stats)
        return {"synthesis": stats.as_dict()}

    return handle
