import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { ProjectSummary } from "../api/types";

export function HomePage({ onOpen }: { onOpen: (id: string) => void }) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.projects().then(setProjects, (e: Error) => setError(e.message));
  }, []);

  async function importFile(file: File) {
    setImporting(true);
    setError(null);
    try {
      const project = await api.importManuscript(file);
      onOpen(project.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setImporting(false);
    }
  }

  return (
    <main className="home">
      <section
        className="dropzone"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const file = e.dataTransfer.files[0];
          if (file) void importFile(file);
        }}
      >
        <h1>Import a manuscript</h1>
        <p>Markdown, plain text or EPUB. Chapters come from headings; paragraphs are split into sentence-aligned segments.</p>
        <input
          ref={input}
          type="file"
          accept=".md,.markdown,.txt,.epub"
          aria-label="Manuscript file"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void importFile(file);
          }}
        />
        <button type="button" className="primary" disabled={importing} onClick={() => input.current?.click()}>
          {importing ? "Importing…" : "Choose file"}
        </button>
        {error && <p className="error-text" role="alert">{error}</p>}
      </section>
      <section className="projects">
        <h2>Projects</h2>
        {projects === null ? (
          <p className="hint">Loading…</p>
        ) : projects.length === 0 ? (
          <p className="hint">No projects yet.</p>
        ) : (
          <ul>
            {projects.map((p) => (
              <li key={p.id}>
                <button type="button" className="project-link" onClick={() => onOpen(p.id)}>
                  <strong>{p.title}</strong>
                  <span className="hint">
                    {p.author ? `${p.author} · ` : ""}
                    {p.chapter_count} chapters · {p.segment_count} segments
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
