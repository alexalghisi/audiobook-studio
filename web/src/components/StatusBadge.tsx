import type { SegmentStatus } from "../api/types";

const LABELS: Record<SegmentStatus, string> = {
  pending: "needs audio",
  queued: "queued",
  generating: "generating",
  cached: "cached",
  failed: "failed",
};

export function StatusBadge({ status, title }: { status: SegmentStatus; title?: string | undefined }) {
  return (
    <span className={`badge badge-${status}`} title={title} data-status={status}>
      {status === "generating" && <span className="spinner" aria-hidden="true" />}
      {LABELS[status]}
    </span>
  );
}
