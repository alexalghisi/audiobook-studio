import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { StudioEvent } from "../api/types";
import { useProjectEvents } from "./useProjectEvents";

class FakeEventSource {
  static last: FakeEventSource | undefined;
  listeners = new Map<string, (e: MessageEvent<string>) => void>();
  closed = false;
  constructor(readonly url: string) {
    FakeEventSource.last = this;
  }
  addEventListener(name: string, fn: (e: MessageEvent<string>) => void) {
    this.listeners.set(name, fn);
  }
  close() {
    this.closed = true;
  }
  emit(name: string, data: string) {
    this.listeners.get(name)?.(new MessageEvent(name, { data }));
  }
}

describe("useProjectEvents", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("delivers typed events and closes the stream on unmount", () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const received: StudioEvent[] = [];
    const { unmount } = renderHook(() => useProjectEvents("p1", (e) => received.push(e)));
    const source = FakeEventSource.last!;
    expect(source.url).toBe("/api/projects/p1/events");

    source.emit("segment", JSON.stringify({ id: "s1", chapter_id: "c1", status: "cached", cache_key: "k" }));
    source.emit("job", "{not json");
    expect(received).toEqual([{ type: "segment", id: "s1", chapter_id: "c1", status: "cached", cache_key: "k" }]);

    unmount();
    expect(source.closed).toBe(true);
  });
});
