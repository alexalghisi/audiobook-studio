import { useEffect, useRef } from "react";

interface Props {
  peaks: number[];
  /** 0..1 */
  progress: number;
  onSeek?: (fraction: number) => void;
  height?: number;
  label?: string;
}

const BAR = 2;
const GAP = 1;

/** Downsamples peaks to one value per bar, keeping the maximum so short loud parts stay visible. */
export function bars(peaks: number[], count: number): number[] {
  if (count <= 0 || peaks.length === 0) return [];
  const out: number[] = [];
  for (let i = 0; i < count; i++) {
    const start = Math.floor((i * peaks.length) / count);
    const end = Math.max(start + 1, Math.floor(((i + 1) * peaks.length) / count));
    let max = 0;
    for (let j = start; j < end && j < peaks.length; j++) max = Math.max(max, peaks[j] ?? 0);
    out.push(max);
  }
  return out;
}

export function Waveform({ peaks, progress, onSeek, height = 56, label = "Waveform" }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const el = canvas.current;
    const ctx = el?.getContext("2d");
    if (!el || !ctx) return;
    const ratio = window.devicePixelRatio || 1;
    const width = el.clientWidth || 600;
    el.width = Math.round(width * ratio);
    el.height = Math.round(height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const styles = getComputedStyle(el);
    const played = styles.getPropertyValue("--wave-played").trim() || "#4f46e5";
    const rest = styles.getPropertyValue("--wave-rest").trim() || "#c7c9d9";
    const values = bars(peaks, Math.floor(width / (BAR + GAP)));
    const mid = height / 2;
    values.forEach((value, i) => {
      const x = i * (BAR + GAP);
      const h = Math.max(1, value * (height - 4));
      ctx.fillStyle = x / width <= progress ? played : rest;
      ctx.fillRect(x, mid - h / 2, BAR, h);
    });
  }, [peaks, progress, height]);

  return (
    <canvas
      ref={canvas}
      className="waveform"
      style={{ height }}
      role="slider"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(progress * 100)}
      tabIndex={0}
      onClick={(e) => {
        if (!onSeek) return;
        const rect = e.currentTarget.getBoundingClientRect();
        onSeek(Math.min(1, Math.max(0, (e.clientX - rect.left) / (rect.width || 1))));
      }}
      onKeyDown={(e) => {
        if (!onSeek) return;
        if (e.key === "ArrowRight") onSeek(Math.min(1, progress + 0.05));
        if (e.key === "ArrowLeft") onSeek(Math.max(0, progress - 0.05));
      }}
    />
  );
}
