import { useEffect, useState } from "react";

import type { Rule } from "../api/types";

interface Props {
  rules: Rule[];
  onSave: (rules: Rule[]) => Promise<void>;
}

const EMPTY: Rule = { term: "", alias: "", case_sensitive: false, whole_word: true };

export function PronunciationEditor({ rules, onSave }: Props) {
  const [draft, setDraft] = useState<Rule[]>(rules);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => setDraft(rules), [rules]);

  function update(index: number, patch: Partial<Rule>) {
    setDraft((rows) => rows.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  async function save() {
    const cleaned = draft.filter((r) => r.term.trim());
    setSaving(true);
    setError(null);
    try {
      await onSave(cleaned);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel" aria-label="Pronunciation dictionary">
      <div className="panel-head">
        <h2>Pronunciation</h2>
        <button type="button" className="small" onClick={() => setDraft((rows) => [...rows, EMPTY])}>
          Add
        </button>
      </div>
      {draft.length === 0 && <p className="hint">Replace names or terms with how they should be said, before synthesis.</p>}
      <ul className="rules">
        {draft.map((rule, i) => (
          <li key={i}>
            <input aria-label="Term" placeholder="Term" value={rule.term} onChange={(e) => update(i, { term: e.target.value })} />
            <span aria-hidden="true">→</span>
            <input aria-label="Say as" placeholder="Say as" value={rule.alias} onChange={(e) => update(i, { alias: e.target.value })} />
            <button type="button" className="icon-button" aria-label={`Remove ${rule.term || "rule"}`} onClick={() => setDraft((rows) => rows.filter((_, j) => j !== i))}>
              ×
            </button>
          </li>
        ))}
      </ul>
      {error && <p className="error-text" role="alert">{error}</p>}
      <button type="button" className="primary small" disabled={saving || JSON.stringify(draft) === JSON.stringify(rules)} onClick={() => void save()}>
        {saving ? "Saving…" : "Save rules"}
      </button>
    </section>
  );
}
