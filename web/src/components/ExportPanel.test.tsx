import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Job } from "../api/types";
import { ExportPanel } from "./ExportPanel";

const running: Job = {
  id: "j1",
  kind: "export",
  status: "running",
  attempts: 1,
  progress: { phase: "rendering", done: 3, total: 4 },
  stats: { synthesis: { segments: 40, cache_hits: 39, synthesized: 1, deduplicated: 0, failed: 0 } },
  result: {},
  error: null,
};

function setup(job: Job | undefined) {
  const props = { onExport: vi.fn(), onCancel: vi.fn(), onPlayChapter: vi.fn() };
  render(<ExportPanel job={job} bookTitle="Salt and Light" {...props} />);
  return { ...props, user: userEvent.setup() };
}

describe("ExportPanel", () => {
  it("starts an export", async () => {
    const { onExport, user } = setup(undefined);
    await user.click(screen.getByRole("button", { name: "Export audiobook" }));
    expect(onExport).toHaveBeenCalledOnce();
  });

  it("shows live progress and cache statistics while running", async () => {
    const { onCancel, user } = setup(running);
    const bar = screen.getByRole("progressbar", { name: "Rendering chapters" });
    expect(bar).toHaveAttribute("aria-valuenow", "75");
    expect(screen.getByText("39 cached · 1 synthesized")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Export audiobook" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledWith("j1");
  });

  it("lists downloads and loudness when done", async () => {
    const chapter = {
      chapter_id: "c1", title: "Chapter 1", key: "a".repeat(64), duration_ms: 65_000,
      integrated_lufs: -18.04, true_peak_dbtp: -3.12, cached: true,
    };
    const { onPlayChapter, user } = setup({
      ...running,
      status: "succeeded",
      progress: { phase: "succeeded", done: 4, total: 4 },
      result: { chapters: [chapter], book: { key: "b".repeat(64), duration_ms: 65_000, chapter_starts_ms: [0], cached: false } },
    });
    expect(screen.getByRole("link", { name: "Download M4B · 1:05" })).toHaveAttribute(
      "href",
      `/api/renders/${"b".repeat(64)}.m4b?name=Salt%20and%20Light`,
    );
    expect(screen.getByText("1:05 · -18.0 LUFS · -3.1 dBTP · reused")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Play Chapter 1" }));
    expect(onPlayChapter).toHaveBeenCalledWith(chapter);
  });

  it("explains a failure", () => {
    setup({ ...running, status: "failed", error: "2 of 40 segments failed to synthesize" });
    expect(screen.getByRole("alert")).toHaveTextContent("2 of 40 segments failed to synthesize");
  });
});
