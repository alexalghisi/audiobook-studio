import { useEffect, useState } from "react";

import { HomePage } from "./pages/HomePage";
import { ProjectPage } from "./pages/ProjectPage";

function projectFromHash(): string | null {
  const match = /^#\/projects\/([0-9a-f-]{36})$/.exec(window.location.hash);
  return match?.[1] ?? null;
}

export function App() {
  const [projectId, setProjectId] = useState(projectFromHash);

  useEffect(() => {
    const onHash = () => setProjectId(projectFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <div className="app">
      <div className="brand">
        <a href="#/">Audiobook Studio</a>
      </div>
      {projectId ? (
        <ProjectPage key={projectId} projectId={projectId} onBack={() => (window.location.hash = "#/")} />
      ) : (
        <HomePage onOpen={(id) => (window.location.hash = `#/projects/${id}`)} />
      )}
    </div>
  );
}
