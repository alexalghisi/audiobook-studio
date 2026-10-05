import { useEffect, useState } from "react";

import type { Project, Voice, VoiceSettings } from "../api/types";

interface Props {
  project: Project;
  voices: Voice[];
  onSave: (patch: { voice_id: string; voice_settings: VoiceSettings }) => Promise<void>;
}

const SLIDERS: { key: "speed" | "stability" | "similarity_boost" | "style"; label: string; min: number; max: number }[] = [
  { key: "speed", label: "Speed", min: 0.7, max: 1.2 },
  { key: "stability", label: "Stability", min: 0, max: 1 },
  { key: "similarity_boost", label: "Similarity", min: 0, max: 1 },
  { key: "style", label: "Style", min: 0, max: 1 },
];

export function VoicePanel({ project, voices, onSave }: Props) {
  const [voiceId, setVoiceId] = useState(project.voice_id);
  const [settings, setSettings] = useState(project.voice_settings);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setVoiceId(project.voice_id);
    setSettings(project.voice_settings);
  }, [project.voice_id, project.voice_settings]);

  const dirty = voiceId !== project.voice_id || JSON.stringify(settings) !== JSON.stringify(project.voice_settings);

  return (
    <section className="panel" aria-label="Project voice">
      <div className="panel-head">
        <h2>Voice</h2>
        <span className="hint">{project.provider}</span>
      </div>
      <label className="field">
        Narrator
        <select value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
          {voices.map((v) => (
            <option key={v.voice_id} value={v.voice_id}>
              {v.name}
              {v.description ? ` (${v.description})` : ""}
            </option>
          ))}
          {!voices.some((v) => v.voice_id === voiceId) && <option value={voiceId}>{voiceId}</option>}
        </select>
      </label>
      {SLIDERS.map(({ key, label, min, max }) => (
        <label className="field slider" key={key}>
          <span>
            {label} <output>{settings[key].toFixed(2)}</output>
          </span>
          <input
            type="range"
            min={min}
            max={max}
            step={0.05}
            value={settings[key]}
            onChange={(e) => setSettings({ ...settings, [key]: Number(e.target.value) })}
          />
        </label>
      ))}
      <button
        type="button"
        className="primary small"
        disabled={!dirty || saving}
        onClick={() => {
          setSaving(true);
          void onSave({ voice_id: voiceId, voice_settings: settings }).finally(() => setSaving(false));
        }}
      >
        {saving ? "Saving…" : "Apply to project"}
      </button>
      <p className="hint">Changing the voice re-addresses every segment; switching back finds the old audio in the cache.</p>
    </section>
  );
}
