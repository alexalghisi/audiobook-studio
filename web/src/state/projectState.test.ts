import { describe, expect, it } from "vitest";

import type { Job, Segment } from "../api/types";
import { applyEvent, initialState, isForeignSegmentEvent, latestJob, reducer } from "./projectState";

const segment: Segment = {
  id: "s1",
  chapter_id: "c1",
  position: 0,
  paragraph_index: 0,
  ends_paragraph: true,
  text: "Hello.",
  voice_id: null,
  speed: null,
  stability: null,
  take: 0,
  status: "pending",
  error: null,
  cache_key: "k1",
  duration_ms: null,
};

const job = (id: string, status: Job["status"], kind: Job["kind"] = "export"): Job => ({
  id,
  kind,
  status,
  attempts: 1,
  progress: { phase: "rendering", done: 1, total: 3 },
  stats: {},
  result: {},
  error: null,
});

const loaded = reducer(initialState, { type: "segments-loaded", chapterId: "c1", segments: [segment] });

describe("project state", () => {
  it("applies status events for the segment's current address", () => {
    const next = applyEvent(loaded, { type: "segment", id: "s1", chapter_id: "c1", status: "generating", cache_key: "k1" });
    expect(next.segments.c1?.[0]?.status).toBe("generating");
  });

  it("ignores late events for an address the segment no longer has, and flags them", () => {
    const event = { type: "segment", id: "s1", chapter_id: "c1", status: "cached", cache_key: "old" } as const;
    expect(applyEvent(loaded, event)).toBe(loaded);
    expect(isForeignSegmentEvent(loaded, event)).toBe(true);
    expect(isForeignSegmentEvent(loaded, { ...event, cache_key: "k1" })).toBe(false);
  });

  it("keeps the failure reason", () => {
    const next = applyEvent(loaded, {
      type: "segment", id: "s1", chapter_id: "c1", status: "failed", cache_key: "k1", error: "HTTP 503",
    });
    expect(next.segments.c1?.[0]).toMatchObject({ status: "failed", error: "HTTP 503" });
  });

  it("ignores events for chapters that are not loaded", () => {
    const event = { type: "segment", id: "x", chapter_id: "c9", status: "cached", cache_key: "k" } as const;
    expect(applyEvent(loaded, event)).toBe(loaded);
  });

  it("tracks jobs from events and snapshots, preferring the active one", () => {
    let state = applyEvent(initialState, { type: "snapshot", jobs: [job("a", "running")] });
    state = applyEvent(state, { type: "job", ...job("b", "succeeded") });
    expect(latestJob(state.jobs, "export")?.id).toBe("a");
    state = applyEvent(state, { type: "job", ...job("a", "cancelled") });
    expect(latestJob(state.jobs, "export")?.id).toBe("b");
    expect(latestJob(state.jobs, "synthesize")).toBeUndefined();
  });
});
