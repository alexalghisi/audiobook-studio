import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Segment } from "../api/types";
import { SegmentRow } from "./SegmentRow";

const base: Segment = {
  id: "s1",
  chapter_id: "c1",
  position: 0,
  paragraph_index: 0,
  ends_paragraph: true,
  text: "The keeper climbed the stairs.",
  voice_id: null,
  speed: null,
  stability: null,
  take: 0,
  status: "cached",
  error: null,
  cache_key: "k",
  duration_ms: 2400,
};

function setup(segment: Partial<Segment> = {}, onSave = vi.fn(async () => undefined)) {
  const onRegenerate = vi.fn();
  const onPlay = vi.fn();
  render(
    <ol>
      <SegmentRow
        segment={{ ...base, ...segment }}
        index={2}
        voices={[{ voice_id: "narrator-gb-f", name: "narrator-gb-f", description: "" }]}
        playing={false}
        onSave={onSave}
        onRegenerate={onRegenerate}
        onPlay={onPlay}
      />
    </ol>,
  );
  return { onSave, onRegenerate, onPlay, user: userEvent.setup() };
}

describe("SegmentRow", () => {
  it("shows text, status and duration", () => {
    setup();
    expect(screen.getByText(base.text)).toBeInTheDocument();
    expect(screen.getByText("cached")).toBeInTheDocument();
    expect(screen.getByText("2.4 s")).toBeInTheDocument();
  });

  it("edits inline and saves the new text", async () => {
    const { onSave, user } = setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const box = screen.getByRole("textbox", { name: "Text of segment 3" });
    await user.clear(box);
    await user.type(box, "The keeper ran up the stairs.");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalledWith({ text: "The keeper ran up the stairs." });
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("does not save unchanged text and closes on Escape", async () => {
    const { onSave, user } = setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("keeps the editor open and shows the error when saving fails", async () => {
    const { user } = setup({}, vi.fn(async () => Promise.reject(new Error("segment text cannot be empty"))));
    await user.dblClick(screen.getByText(base.text));
    await user.type(screen.getByRole("textbox"), " more");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("segment text cannot be empty");
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("offers a new take for cached audio and generation otherwise", async () => {
    const { onRegenerate, user } = setup();
    await user.click(screen.getByRole("button", { name: "New take" }));
    expect(onRegenerate).toHaveBeenCalledWith(true);
  });

  it("can only play cached segments and is read-only while generating", () => {
    setup({ status: "generating", duration_ms: null });
    expect(screen.getByRole("button", { name: "Play segment 3" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
  });

  it("shows why a segment failed", () => {
    setup({ status: "failed", error: "HTTP 422 invalid text" });
    expect(screen.getByText("HTTP 422 invalid text")).toBeInTheDocument();
  });

  it("sets and clears per-segment overrides", async () => {
    const { onSave, user } = setup();
    await user.click(screen.getByRole("button", { name: "Voice" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Speed" }), "1.1");
    expect(onSave).toHaveBeenLastCalledWith({ speed: 1.1 });
    await user.selectOptions(screen.getByRole("combobox", { name: "Voice" }), "narrator-gb-f");
    expect(onSave).toHaveBeenLastCalledWith({ voice_id: "narrator-gb-f" });
  });
});
