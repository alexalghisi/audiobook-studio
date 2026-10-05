import type { ChapterInfo, SegmentStatus } from "../api/types";

const ORDER: SegmentStatus[] = ["cached", "generating", "queued", "pending", "failed"];

function minutes(ms: number): string {
  if (!ms) return "";
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function ChapterList({
  chapters,
  selected,
  onSelect,
}: {
  chapters: ChapterInfo[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <nav className="chapters" aria-label="Chapters">
      <ol>
        {chapters.map((c) => {
          const cached = c.status_counts.cached ?? 0;
          return (
            <li key={c.id}>
              <button
                type="button"
                className={c.id === selected ? "chapter selected" : "chapter"}
                aria-current={c.id === selected ? "true" : undefined}
                onClick={() => onSelect(c.id)}
              >
                <span className="chapter-title">{c.title}</span>
                <span className="status-bar" aria-hidden="true">
                  {ORDER.map((status) => {
                    const n = c.status_counts[status] ?? 0;
                    return n ? <span key={status} className={`bar-${status}`} style={{ flexGrow: n }} /> : null;
                  })}
                </span>
                <span className="chapter-meta">
                  {cached}/{c.segment_count}
                  {c.duration_ms ? ` · ${minutes(c.duration_ms)}` : ""}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
