export type SegmentStatus = "pending" | "queued" | "generating" | "cached" | "failed";
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type JobKind = "synthesize" | "export";

export interface VoiceSettings {
  stability: number;
  similarity_boost: number;
  style: number;
  speed: number;
  use_speaker_boost: boolean;
}

export interface Rule {
  term: string;
  alias: string;
  case_sensitive: boolean;
  whole_word: boolean;
}

export interface ChapterInfo {
  id: string;
  position: number;
  title: string;
  segment_count: number;
  status_counts: Partial<Record<SegmentStatus, number>>;
  duration_ms: number;
}

export interface SynthesisStats {
  segments: number;
  cache_hits: number;
  synthesized: number;
  deduplicated: number;
  failed: number;
}

export interface RenderedChapter {
  chapter_id: string;
  title: string;
  key: string;
  duration_ms: number;
  integrated_lufs: number;
  true_peak_dbtp: number;
  cached: boolean;
}

export interface JobResult {
  synthesis?: SynthesisStats;
  chapters?: RenderedChapter[];
  book?: { key: string; duration_ms: number; chapter_starts_ms: number[]; cached: boolean };
}

export interface JobProgress {
  phase: string;
  done: number;
  total: number;
}

export interface Job {
  id: string;
  kind: JobKind;
  status: JobStatus;
  attempts: number;
  progress: JobProgress;
  stats: { synthesis?: SynthesisStats; render?: { chapters: number; cached: number } };
  result: JobResult;
  error: string | null;
}

export interface ProjectSummary {
  id: string;
  title: string;
  author: string | null;
  chapter_count: number;
  segment_count: number;
  created_at: string;
}

export interface Project {
  id: string;
  title: string;
  author: string | null;
  provider: string;
  voice_id: string;
  model_id: string;
  voice_settings: VoiceSettings;
  chapters: ChapterInfo[];
  rules: Rule[];
  jobs: Job[];
}

export interface Segment {
  id: string;
  chapter_id: string;
  position: number;
  paragraph_index: number;
  ends_paragraph: boolean;
  text: string;
  voice_id: string | null;
  speed: number | null;
  stability: number | null;
  take: number;
  status: SegmentStatus;
  error: string | null;
  cache_key: string;
  duration_ms: number | null;
}

export interface Voice {
  voice_id: string;
  name: string;
  description: string;
}

export interface Peaks {
  duration_ms: number;
  peaks: number[];
}

export type SegmentPatch = Partial<Pick<Segment, "text" | "voice_id" | "speed" | "stability">>;

export type StudioEvent =
  | { type: "segment"; id: string; chapter_id: string; status: SegmentStatus; cache_key: string; error?: string | null }
  | ({ type: "job" } & Job)
  | { type: "snapshot"; jobs: Job[] };
