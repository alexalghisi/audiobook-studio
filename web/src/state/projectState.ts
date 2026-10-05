import type { Job, Segment, StudioEvent } from "../api/types";

export interface ProjectState {
  segments: Record<string, Segment[]>;
  jobs: Record<string, Job>;
}

export type Action =
  | { type: "event"; event: StudioEvent }
  | { type: "segments-loaded"; chapterId: string; segments: Segment[] }
  | { type: "segment-saved"; segment: Segment }
  | { type: "job-submitted"; job: Job };

export const initialState: ProjectState = { segments: {}, jobs: {} };

function replaceSegment(state: ProjectState, chapterId: string, id: string, update: (s: Segment) => Segment): ProjectState {
  const list = state.segments[chapterId];
  if (!list) return state;
  const index = list.findIndex((s) => s.id === id);
  const current = list[index];
  if (current === undefined) return state;
  const updated = update(current);
  if (updated === current) return state;
  const next = list.slice();
  next[index] = updated;
  return { ...state, segments: { ...state.segments, [chapterId]: next } };
}

export function reducer(state: ProjectState, action: Action): ProjectState {
  switch (action.type) {
    case "segments-loaded":
      return { ...state, segments: { ...state.segments, [action.chapterId]: action.segments } };
    case "segment-saved":
      return replaceSegment(state, action.segment.chapter_id, action.segment.id, () => action.segment);
    case "job-submitted":
      return { ...state, jobs: { ...state.jobs, [action.job.id]: action.job } };
    case "event":
      return applyEvent(state, action.event);
  }
}

export function applyEvent(state: ProjectState, event: StudioEvent): ProjectState {
  switch (event.type) {
    case "segment":
      return replaceSegment(state, event.chapter_id, event.id, (s) => {
        // Different address: either a late event from before our own edit, or an edit made
        // elsewhere. The page reloads the chapter in that case (see `isForeignSegmentEvent`).
        if (event.cache_key !== s.cache_key) return s;
        return {
          ...s,
          status: event.status,
          cache_key: event.cache_key,
          error: event.error ?? null,
          duration_ms: event.status === "cached" ? s.duration_ms : null,
        };
      });
    case "job": {
      const { type: _type, ...job } = event;
      return { ...state, jobs: { ...state.jobs, [job.id]: job } };
    }
    case "snapshot": {
      const jobs = { ...state.jobs };
      for (const job of event.jobs) jobs[job.id] = job;
      return { ...state, jobs };
    }
  }
}

export function isForeignSegmentEvent(state: ProjectState, event: StudioEvent): boolean {
  if (event.type !== "segment") return false;
  const segment = state.segments[event.chapter_id]?.find((s) => s.id === event.id);
  return segment !== undefined && segment.cache_key !== event.cache_key;
}

export function isActive(job: Job): boolean {
  return job.status === "queued" || job.status === "running";
}

export function latestJob(jobs: Record<string, Job>, kind: Job["kind"]): Job | undefined {
  const all = Object.values(jobs).filter((j) => j.kind === kind);
  return all.find(isActive) ?? all.at(-1);
}
