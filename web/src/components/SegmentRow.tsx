import { useState } from "react";

import type { Segment, SegmentPatch, Voice } from "../api/types";
import { StatusBadge } from "./StatusBadge";

interface Props {
  segment: Segment;
  index: number;
  voices: Voice[];
  playing: boolean;
  onSave: (patch: SegmentPatch) => Promise<void>;
  onRegenerate: (newTake: boolean) => void;
  onPlay: () => void;
}

function formatMs(ms: number): string {
  const s = Math.round(ms / 100) / 10;
  return `${s.toFixed(1)} s`;
}

export function SegmentRow({ segment, index, voices, playing, onSave, onRegenerate, onPlay }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(segment.text);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showVoice, setShowVoice] = useState(false);

  const busy = segment.status === "queued" || segment.status === "generating";
  const hasOverride = segment.voice_id !== null || segment.speed !== null || segment.stability !== null;

  async function save(patch: SegmentPatch, closeEditor: boolean) {
    setSaving(true);
    setError(null);
    try {
      await onSave(patch);
      if (closeEditor) setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  function startEditing() {
    setDraft(segment.text);
    setError(null);
    setEditing(true);
  }

  return (
    <li
      className={`segment${segment.ends_paragraph ? " ends-paragraph" : ""}${playing ? " playing" : ""}`}
      data-testid="segment"
      data-status={segment.status}
    >
      <div className="segment-gutter">
        <span className="segment-index">{index + 1}</span>
        <button
          type="button"
          className="icon-button"
          onClick={onPlay}
          disabled={segment.status !== "cached"}
          aria-label={playing ? `Stop segment ${index + 1}` : `Play segment ${index + 1}`}
        >
          {playing ? "■" : "▶"}
        </button>
      </div>
      <div className="segment-body">
        {editing ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (draft.trim() === segment.text) setEditing(false);
              else void save({ text: draft }, true);
            }}
          >
            <textarea
              aria-label={`Text of segment ${index + 1}`}
              value={draft}
              autoFocus
              rows={Math.max(2, Math.ceil(draft.length / 90))}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setEditing(false);
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) e.currentTarget.form?.requestSubmit();
              }}
            />
            <div className="row-actions">
              <button type="submit" className="primary small" disabled={saving || !draft.trim()}>
                {saving ? "Saving…" : "Save"}
              </button>
              <button type="button" className="small" onClick={() => setEditing(false)}>
                Cancel
              </button>
              <span className="hint">{draft.length} characters · Ctrl+Enter to save</span>
            </div>
          </form>
        ) : (
          <p className="segment-text" onDoubleClick={startEditing}>
            {segment.text}
          </p>
        )}
        {error && <p className="error-text" role="alert">{error}</p>}
        {segment.status === "failed" && segment.error && <p className="error-text">{segment.error}</p>}
        {showVoice && (
          <div className="segment-voice" aria-label={`Voice override for segment ${index + 1}`}>
            <label>
              Voice
              <select
                value={segment.voice_id ?? ""}
                onChange={(e) => void save({ voice_id: e.target.value || null }, false)}
              >
                <option value="">Project voice</option>
                {voices.map((v) => (
                  <option key={v.voice_id} value={v.voice_id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Speed
              <select
                value={segment.speed ?? ""}
                onChange={(e) => void save({ speed: e.target.value ? Number(e.target.value) : null }, false)}
              >
                <option value="">Project</option>
                {[0.8, 0.9, 1.0, 1.1, 1.2].map((v) => (
                  <option key={v} value={v}>
                    {v.toFixed(1)}×
                  </option>
                ))}
              </select>
            </label>
            <label>
              Stability
              <select
                value={segment.stability ?? ""}
                onChange={(e) => void save({ stability: e.target.value ? Number(e.target.value) : null }, false)}
              >
                <option value="">Project</option>
                {[0.2, 0.35, 0.5, 0.65, 0.8].map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}
      </div>
      <div className="segment-side">
        <StatusBadge status={segment.status} title={segment.error ?? undefined} />
        {segment.duration_ms !== null && <span className="duration">{formatMs(segment.duration_ms)}</span>}
        <div className="segment-buttons">
          {!editing && (
            <button type="button" className="small" onClick={startEditing} disabled={busy}>
              Edit
            </button>
          )}
          <button
            type="button"
            className="small"
            onClick={() => onRegenerate(segment.status === "cached")}
            disabled={busy}
            title={segment.status === "cached" ? "Generate a new take" : "Generate audio for this segment"}
          >
            {segment.status === "cached" ? "New take" : "Generate"}
          </button>
          <button
            type="button"
            className={`small${hasOverride ? " active" : ""}`}
            aria-expanded={showVoice}
            onClick={() => setShowVoice((v) => !v)}
          >
            Voice{hasOverride ? " •" : ""}
          </button>
        </div>
      </div>
    </li>
  );
}
