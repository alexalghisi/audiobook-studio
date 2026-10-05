import { useEffect, useRef } from "react";

import { urls } from "../api/client";
import type { StudioEvent } from "../api/types";

/** Subscribes to a project's server-sent events. EventSource reconnects on its own. */
export function useProjectEvents(projectId: string, onEvent: (event: StudioEvent) => void): void {
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    const source = new EventSource(urls.events(projectId));
    const listener = (message: MessageEvent<string>) => {
      try {
        const data = JSON.parse(message.data) as Record<string, unknown>;
        handler.current({ ...data, type: message.type } as StudioEvent);
      } catch {
        // ignore malformed frames
      }
    };
    for (const name of ["segment", "job", "snapshot"]) source.addEventListener(name, listener);
    return () => source.close();
  }, [projectId]);
}
