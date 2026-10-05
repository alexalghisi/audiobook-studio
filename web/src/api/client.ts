import type {
  Job,
  Peaks,
  Project,
  ProjectSummary,
  Rule,
  Segment,
  SegmentPatch,
  VoiceSettings,
  Voice,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg?: string }) => d.msg).join("; ");
    } catch {
      // keep the status text
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function json(method: string, body: unknown, headers: Record<string, string> = {}): RequestInit {
  return { method, headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) };
}

export const api = {
  projects: () => request<ProjectSummary[]>("/api/projects"),
  project: (id: string) => request<Project>(`/api/projects/${id}`),
  importManuscript: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Project>("/api/projects", { method: "POST", body: form });
  },
  updateProject: (id: string, patch: { voice_id?: string; voice_settings?: VoiceSettings; title?: string }) =>
    request<Project>(`/api/projects/${id}`, json("PATCH", patch)),
  saveRules: (id: string, rules: Rule[]) =>
    request<Project>(`/api/projects/${id}/pronunciations`, json("PUT", { rules })),
  segments: (chapterId: string) => request<Segment[]>(`/api/chapters/${chapterId}/segments`),
  updateSegment: (id: string, patch: SegmentPatch) => request<Segment>(`/api/segments/${id}`, json("PATCH", patch)),
  regenerate: (id: string, newTake: boolean) =>
    request<Job>(`/api/segments/${id}/regenerate?new_take=${newTake}`, { method: "POST" }),
  synthesize: (projectId: string, chapterId?: string) =>
    request<Job>(`/api/projects/${projectId}/synthesize`, json("POST", { chapter_id: chapterId ?? null })),
  exportBook: (projectId: string, idempotencyKey: string) =>
    request<Job>(`/api/projects/${projectId}/export`, { method: "POST", headers: { "Idempotency-Key": idempotencyKey } }),
  cancelJob: (jobId: string) => request<Job>(`/api/jobs/${jobId}/cancel`, { method: "POST" }),
  voices: () => request<Voice[]>("/api/voices"),
  segmentPeaks: (id: string) => request<Peaks>(`/api/segments/${id}/peaks`),
  renderPeaks: (key: string) => request<Peaks>(`/api/renders/${key}/peaks`),
};

export const urls = {
  // The cache key in the query string makes the URL change exactly when the audio does.
  segmentAudio: (segment: Segment) => `/api/segments/${segment.id}/audio?v=${segment.cache_key.slice(0, 16)}`,
  render: (key: string, ext: "mp3" | "m4b", name?: string) =>
    `/api/renders/${key}.${ext}${name ? `?name=${encodeURIComponent(name)}` : ""}`,
  events: (projectId: string) => `/api/projects/${projectId}/events`,
};
