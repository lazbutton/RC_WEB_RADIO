import { useEffect, useState } from "react";
import { Maximize2, Minimize2, SkipForward, Unplug } from "lucide-react";
import type { MidiStatus } from "../midi/useApcMini";
import { formatMmSs } from "../lib/format";

export type UiMode = "live" | "edit";

export function TopBar({
  title,
  remaining,
  playing,
  midi,
  chainAfterSound,
  mode,
  libraryOpen,
  onMode,
  onToggleLibrary,
  onToggleChain,
  onSkip,
  onConnectMidi,
}: {
  title: string;
  remaining: number | null;
  playing: boolean;
  midi: MidiStatus;
  chainAfterSound: boolean;
  mode: UiMode;
  libraryOpen: boolean;
  onMode: (mode: UiMode) => void;
  onToggleLibrary: () => void;
  onToggleChain: () => void;
  onSkip: () => void;
  onConnectMidi: () => void;
}) {
  const remainLabel = remaining != null ? formatMmSs(remaining) : null;
  const clock = useClock();
  const wide = useFullscreen();

  return (
    <header className="top">
      <div className="top-left">
        <div className="top-brand">
          <img src="./logo-pads.svg" alt="" width={22} height={22} />
          <span>Pads</span>
        </div>
        <div className="mode-switch" role="group" aria-label="Mode">
          <button
            type="button"
            className={`mode-btn${mode === "live" ? " is-on" : ""}`}
            aria-pressed={mode === "live"}
            onClick={() => onMode("live")}
          >
            Live
          </button>
          <button
            type="button"
            className={`mode-btn${mode === "edit" ? " is-on" : ""}`}
            aria-pressed={mode === "edit"}
            onClick={() => onMode("edit")}
          >
            Régler
          </button>
        </div>
      </div>

      <div className="top-now" aria-live="polite">
        <p className={`top-now-kicker${playing ? " is-hot" : ""}`}>
          {playing ? "En l’air" : "Silence"}
        </p>
        <p className="top-now-title">{playing ? title : "Silence"}</p>
      </div>

      <p className="top-clock" aria-label={`Heure ${clock}`}>
        {clock}
      </p>

      {remainLabel ? (
        <p className={`top-remain${playing ? " is-hot" : ""}`} aria-label={`Temps restant −${remainLabel}`}>
          −{remainLabel}
        </p>
      ) : (
        <p className="top-remain is-empty" aria-hidden="true">
          −00:00
        </p>
      )}

      <div className="top-actions">
        {mode === "live" ? (
          <button
            type="button"
            className={`chip${libraryOpen ? " is-on" : ""}`}
            aria-pressed={libraryOpen}
            onClick={onToggleLibrary}
          >
            Sons
          </button>
        ) : null}
        <button type="button" className="btn btn-skip" onClick={onSkip} disabled={!playing}>
          <SkipForward size={14} aria-hidden="true" />
          Stop
        </button>
        <button
          type="button"
          className={`chip${chainAfterSound ? " is-on" : ""}`}
          aria-pressed={chainAfterSound}
          onClick={onToggleChain}
        >
          Jingle après
        </button>
        <MidiChip status={midi} onConnect={onConnectMidi} />
        <button
          type="button"
          className="chip"
          onClick={() => void toggleFullscreen()}
          aria-pressed={wide}
          title={wide ? "Quitter le plein écran" : "Plein écran"}
        >
          {wide ? <Minimize2 size={12} aria-hidden="true" /> : <Maximize2 size={12} aria-hidden="true" />}
          {wide ? "Fenêtre" : "Plein écran"}
        </button>
      </div>
    </header>
  );
}

function MidiChip({ status, onConnect }: { status: MidiStatus; onConnect: () => void }) {
  if (status.kind === "connected") {
    return (
      <span className="chip is-live" title={status.sysex ? `${status.name} · LED` : status.name}>
        APC
      </span>
    );
  }
  return (
    <button type="button" className="chip" onClick={onConnect} title={midiHelp(status)}>
      <Unplug size={12} aria-hidden="true" />
      MIDI
    </button>
  );
}

function midiHelp(status: MidiStatus): string {
  switch (status.kind) {
    case "connected":
      return status.name;
    case "searching":
      return "Recherche de l’APC Mini MK2…";
    case "disconnected":
      return "APC Mini MK2 débranché";
    case "need-gesture":
      return "Autoriser le MIDI";
    case "denied":
      return "MIDI refusé — cocher SysEx";
    case "unsupported":
      return "Web MIDI indisponible — Chrome, Edge ou Safari";
  }
}

function formatClock(date: Date): string {
  return date.toLocaleTimeString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function useClock(): string {
  const [clock, setClock] = useState(() => formatClock(new Date()));
  useEffect(() => {
    const tick = () => setClock(formatClock(new Date()));
    const timer = window.setInterval(tick, 250);
    return () => window.clearInterval(timer);
  }, []);
  return clock;
}

function useFullscreen(): boolean {
  const [wide, setWide] = useState(() => Boolean(document.fullscreenElement));
  useEffect(() => {
    const sync = () => setWide(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);
  return wide;
}

async function toggleFullscreen(): Promise<void> {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch {
    /* refusé */
  }
}
