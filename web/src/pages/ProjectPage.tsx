import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import { api, urls } from "../api/client";
import type { Project, RenderedChapter, Segment, SegmentPatch, StudioEvent, Voice } from "../api/types";
import { ChapterList } from "../components/ChapterList";
import { ExportPanel, JobProgressBar } from "../components/ExportPanel";
import { Player, type PlayerSource } from "../components/Player";
import { PronunciationEditor } from "../components/PronunciationEditor";
import { SegmentRow } from "../components/SegmentRow";
import { VoicePanel } from "../components/VoicePanel";
import { useProjectEvents } from "../hooks/useProjectEvents";
import { initialState, isActive, isForeignSegmentEvent, latestJob, reducer } from "../state/projectState";

const REFRESH_DELAY_MS = 400;

export function ProjectPage({ projectId, onBack }: { projectId: string; onBack: () => void }) {
  const [project, setProject] = useState<Project | null>(null);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [chapterId, setChapterId] = useState<string | null>(null);
  const [state, dispatch] = useReducer(reducer, initialState);
  const [source, setSource] = useState<PlayerSource | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  const refreshTimer = useRef<number | undefined>(undefined);

  const loadProject = useCallback(async () => {
    try {
      const p = await api.project(projectId);
      setProject(p);
      for (const job of p.jobs) dispatch({ type: "job-submitted", job });
      setChapterId((current) => current ?? p.chapters[0]?.id ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [projectId]);

  const loadSegments = useCallback(async (id: string) => {
    const segments = await api.segments(id);
    dispatch({ type: "segments-loaded", chapterId: id, segments });
  }, []);

  // Chapter counters come from the server; refresh them shortly after a burst of events.
  const scheduleRefresh = useCallback(() => {
    window.clearTimeout(refreshTimer.current);
    refreshTimer.current = window.setTimeout(() => void loadProject(), REFRESH_DELAY_MS);
  }, [loadProject]);

  useEffect(() => {
    void loadProject();
    api.voices().then(setVoices, () => setVoices([]));
    return () => window.clearTimeout(refreshTimer.current);
  }, [loadProject]);

  useEffect(() => {
    if (chapterId) void loadSegments(chapterId);
  }, [chapterId, loadSegments]);

  useProjectEvents(projectId, (event: StudioEvent) => {
    if (isForeignSegmentEvent(stateRef.current, event) && event.type === "segment") {
      void loadSegments(event.chapter_id);
    }
    dispatch({ type: "event", event });
    if (event.type === "segment") scheduleRefresh();
    if (event.type === "segment" && event.status === "cached" && event.chapter_id === chapterId) {
      // Durations arrive with the segment list.
      window.setTimeout(() => void loadSegments(event.chapter_id), REFRESH_DELAY_MS);
    }
    if (event.type === "job" && !isActive(event)) scheduleRefresh();
  });

  const chapter = project?.chapters.find((c) => c.id === chapterId) ?? null;
  const segments = useMemo(() => (chapterId ? state.segments[chapterId] ?? [] : []), [chapterId, state.segments]);
  const exportJob = latestJob(state.jobs, "export");
  const synthJob = Object.values(state.jobs).find((j) => j.kind === "synthesize" && isActive(j));

  const playSegment = useCallback(
    (segment: Segment, index: number) => {
      setSource((current) =>
        current?.id === `${segment.id}:${segment.cache_key}`
          ? null
          : {
              id: `${segment.id}:${segment.cache_key}`,
              title: `${chapter?.title ?? ""} · segment ${index + 1}`,
              url: urls.segmentAudio(segment),
              loadPeaks: () => api.segmentPeaks(segment.id),
            },
      );
    },
    [chapter?.title],
  );

  function playNext() {
    if (!source) return;
    const index = segments.findIndex((s) => source.id === `${s.id}:${s.cache_key}`);
    const next = segments.slice(index + 1).find((s) => s.status === "cached");
    if (index >= 0 && next) playSegment(next, segments.indexOf(next));
  }

  function playChapter(rendered: RenderedChapter) {
    setSource({
      id: rendered.key,
      title: `${rendered.title} (rendered)`,
      url: urls.render(rendered.key, "mp3"),
      loadPeaks: () => api.renderPeaks(rendered.key),
    });
  }

  async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
    setError(null);
    try {
      return await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return undefined;
    }
  }

  if (!project) {
    return <main className="loading">{error ? <p className="error-text">{error}</p> : "Loading…"}</main>;
  }

  return (
    <div className="project">
      <header className="project-head">
        <button type="button" className="link" onClick={onBack}>
          ← Projects
        </button>
        <div className="project-title">
          <h1>{project.title}</h1>
          {project.author && <span className="hint">{project.author}</span>}
        </div>
        <button
          type="button"
          className="small"
          disabled={synthJob !== undefined}
          onClick={() =>
            void run(async () => dispatch({ type: "job-submitted", job: await api.synthesize(project.id) }))
          }
        >
          Generate all
        </button>
      </header>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <div className="workspace">
        <ChapterList chapters={project.chapters} selected={chapterId} onSelect={setChapterId} />
        <main className="segments-pane">
          {chapter && (
            <div className="chapter-head">
              <h2>{chapter.title}</h2>
              <span className="hint">{chapter.segment_count} segments</span>
              <button
                type="button"
                className="small"
                disabled={synthJob !== undefined}
                onClick={() =>
                  void run(async () =>
                    dispatch({ type: "job-submitted", job: await api.synthesize(project.id, chapter.id) }),
                  )
                }
              >
                Generate chapter
              </button>
            </div>
          )}
          {synthJob && (
            <div className="inline-job">
              <JobProgressBar job={synthJob} />
              <button type="button" className="small danger" onClick={() => void run(() => api.cancelJob(synthJob.id))}>
                Cancel
              </button>
            </div>
          )}
          <ol className="segment-list">
            {segments.map((segment, index) => (
              <SegmentRow
                key={segment.id}
                segment={segment}
                index={index}
                voices={voices}
                playing={source?.id === `${segment.id}:${segment.cache_key}`}
                onPlay={() => playSegment(segment, index)}
                onSave={async (patch: SegmentPatch) => {
                  const saved = await api.updateSegment(segment.id, patch);
                  dispatch({ type: "segment-saved", segment: saved });
                  scheduleRefresh();
                }}
                onRegenerate={(newTake) =>
                  void run(async () => dispatch({ type: "job-submitted", job: await api.regenerate(segment.id, newTake) }))
                }
              />
            ))}
          </ol>
        </main>
        <aside className="side">
          <ExportPanel
            job={exportJob}
            bookTitle={project.title}
            onExport={() =>
              void run(async () =>
                dispatch({ type: "job-submitted", job: await api.exportBook(project.id, crypto.randomUUID()) }),
              )
            }
            onCancel={(id) => void run(() => api.cancelJob(id))}
            onPlayChapter={playChapter}
          />
          <VoicePanel
            project={project}
            voices={voices}
            onSave={async (patch) => {
              const updated = await run(() => api.updateProject(project.id, patch));
              if (updated) setProject(updated);
              if (chapterId) await loadSegments(chapterId);
            }}
          />
          <PronunciationEditor
            rules={project.rules}
            onSave={async (rules) => {
              const updated = await api.saveRules(project.id, rules);
              setProject(updated);
              if (chapterId) await loadSegments(chapterId);
            }}
          />
        </aside>
      </div>
      <Player source={source} onEnded={playNext} />
    </div>
  );
}
