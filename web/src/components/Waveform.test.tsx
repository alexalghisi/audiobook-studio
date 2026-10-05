import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Waveform, bars } from "./Waveform";

describe("bars", () => {
  it("keeps the loudest value of each group", () => {
    expect(bars([0.1, 0.9, 0.2, 0.3, 0.5, 0.4], 3)).toEqual([0.9, 0.3, 0.5]);
  });

  it("stretches short input instead of inventing values", () => {
    expect(bars([0.2, 0.8], 4)).toEqual([0.2, 0.2, 0.8, 0.8]);
    expect(bars([], 10)).toEqual([]);
  });
});

describe("Waveform", () => {
  const fillRect = vi.fn();
  const styles: string[] = [];

  beforeEach(() => {
    fillRect.mockClear();
    styles.length = 0;
    const ctx = {
      setTransform: vi.fn(),
      clearRect: vi.fn(),
      fillRect: (...args: number[]) => {
        styles.push(ctx.fillStyle);
        fillRect(...args);
      },
      fillStyle: "",
    };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(300);
  });

  afterEach(() => vi.restoreAllMocks());

  it("draws one bar per 3 px and colours the played part", () => {
    render(<Waveform peaks={Array.from({ length: 500 }, (_, i) => (i % 10) / 10)} progress={0.5} />);
    expect(fillRect).toHaveBeenCalledTimes(100);
    const played = styles.filter((s) => s === "#4f46e5").length;
    expect(played).toBeGreaterThanOrEqual(49);
    expect(played).toBeLessThanOrEqual(51);
  });

  it("seeks to where it is clicked and with the arrow keys", () => {
    const onSeek = vi.fn();
    render(<Waveform peaks={[0.5, 0.5]} progress={0.5} onSeek={onSeek} label="Position" />);
    const slider = screen.getByRole("slider", { name: "Position" });
    vi.spyOn(slider, "getBoundingClientRect").mockReturnValue({ left: 100, width: 200 } as DOMRect);
    fireEvent.click(slider, { clientX: 150 });
    expect(onSeek).toHaveBeenLastCalledWith(0.25);
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    expect(onSeek).toHaveBeenLastCalledWith(0.55);
    expect(slider).toHaveAttribute("aria-valuenow", "50");
  });
});
