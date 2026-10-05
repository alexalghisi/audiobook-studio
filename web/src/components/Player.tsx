import { useEffect, useRef, useState } from "react";

import type { Peaks } from "../api/types";
import { Waveform } from "./Waveform";

export interface PlayerSource {
  id: string;
  title: string;
  url: string;
  loadPeaks: () => Promise<Peaks>;
}

function clock(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function Player({ source, onEnded }: { source: PlayerSource | null; onEnded?: () => void }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [peaks, setPeaks] = useState<number[]>([]);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [paused, setPaused] = useState(true);

  useEffect(() => {
    setPeaks([]);
    setTime(0);
    if (!source) return;
    let alive = true;
    source
      .loadPeaks()
      .then((p) => {
        if (!alive) return;
        setPeaks(p.peaks);
        setDuration(p.duration_ms / 1000);
      })
      .catch(() => undefined);
    void audio.current?.play().catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [source]);

  useEffect(() => {
    // timeupdate fires ~4×/s; follow the playhead smoothly while playing.
    if (paused) return;
    let frame = 0;
    const tick = () => {
      if (audio.current) setTime(audio.current.currentTime);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [paused]);

  if (!source) return <footer className="player empty">Select a cached segment or a rendered chapter to listen.</footer>;

  const total = duration || audio.current?.duration || 0;
  return (
    <footer className="player" aria-label="Player">
      <audio
        ref={audio}
        src={source.url}
        preload="auto"
        onPlay={() => setPaused(false)}
        onPause={() => setPaused(true)}
        onEnded={() => {
          setPaused(true);
          onEnded?.();
        }}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
      />
      <button
        type="button"
        className="play-button"
        aria-label={paused ? "Play" : "Pause"}
        onClick={() => (paused ? void audio.current?.play() : audio.current?.pause())}
      >
        {paused ? "▶" : "❚❚"}
      </button>
      <div className="player-main">
        <div className="player-title">
          <span>{source.title}</span>
          <span className="player-time">
            {clock(time)} / {clock(total)}
          </span>
        </div>
        <Waveform
          peaks={peaks}
          progress={total ? time / total : 0}
          label={`Position in ${source.title}`}
          onSeek={(fraction) => {
            if (audio.current && total) audio.current.currentTime = fraction * total;
            setTime(fraction * total);
          }}
        />
      </div>
    </footer>
  );
}
