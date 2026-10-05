import { urls } from "../api/client";
import type { Job, RenderedChapter } from "../api/types";
import { isActive } from "../state/projectState";

const PHASES: Record<string, string> = {
  queued: "Waiting for a worker",
  starting: "Starting",
  synthesizing: "Generating audio",
  rendering: "Rendering chapters",
  packaging: "Packaging the M4B",
  retrying: "Retrying",
  succeeded: "Done",
};

function duration(ms: number): string {
  const total = Math.round(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

interface Props {
  job: Job | undefined;
  bookTitle: string;
  onExport: () => void;
  onCancel: (jobId: string) => void;
  onPlayChapter: (chapter: RenderedChapter) => void;
}

export function JobProgressBar({ job }: { job: Job }) {
  const { done, total, phase } = job.progress;
  const percent = total ? Math.round((done / total) * 100) : 0;
  const synthesis = job.stats.synthesis ?? job.result.synthesis;
  return (
    <div className="job-progress">
      <div className="job-progress-label">
        <span>{PHASES[phase] ?? phase}</span>
        <span>
          {done}/{total}
        </span>
      </div>
      <div
        className="progress"
        role="progressbar"
        aria-label={PHASES[phase] ?? phase}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
      >
        <div className="progress-fill" style={{ width: `${percent}%` }} />
      </div>
      {synthesis && (
        <p className="hint">
          {synthesis.cache_hits} cached · {synthesis.synthesized} synthesized
          {synthesis.deduplicated ? ` · ${synthesis.deduplicated} deduplicated` : ""}
          {synthesis.failed ? ` · ${synthesis.failed} failed` : ""}
        </p>
      )}
    </div>
  );
}

export function ExportPanel({ job, bookTitle, onExport, onCancel, onPlayChapter }: Props) {
  const active = job !== undefined && isActive(job);
  const finished = job?.status === "succeeded" ? job : undefined;
  const done = finished?.result;
  return (
    <section className="panel export-panel" aria-label="Export">
      <div className="panel-head">
        <h2>Export</h2>
        {active ? (
          <button type="button" className="small danger" onClick={() => onCancel(job.id)}>
            Cancel
          </button>
        ) : (
          <button type="button" className="primary" onClick={onExport}>
            Export audiobook
          </button>
        )}
      </div>
      <p className="hint">MP3 per chapter (192 kbps, −18 LUFS, −3 dBTP ceiling) and an M4B with chapter markers.</p>
      {active && <JobProgressBar job={job} />}
      {job?.status === "failed" && <p className="error-text" role="alert">Export failed: {job.error}</p>}
      {job?.status === "cancelled" && <p className="hint">Export cancelled.</p>}
      {finished && done?.book && done.chapters && (
        <div className="export-result">
          <JobProgressBar job={finished} />
          <a className="button primary" href={urls.render(done.book.key, "m4b", bookTitle)} download>
            Download M4B · {duration(done.book.duration_ms)}
          </a>
          <ol className="rendered-chapters">
            {done.chapters.map((chapter) => (
              <li key={chapter.key}>
                <button type="button" className="icon-button" aria-label={`Play ${chapter.title}`} onClick={() => onPlayChapter(chapter)}>
                  ▶
                </button>
                <span className="rendered-title">{chapter.title}</span>
                <span className="rendered-meta">
                  {duration(chapter.duration_ms)} · {chapter.integrated_lufs.toFixed(1)} LUFS · {chapter.true_peak_dbtp.toFixed(1)} dBTP
                  {chapter.cached ? " · reused" : ""}
                </span>
                <a href={urls.render(chapter.key, "mp3", chapter.title)} download>
                  MP3
                </a>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
