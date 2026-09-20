import { SpeakerLoudIcon } from "@radix-ui/react-icons";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { Transition } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { Count } from "./Count";
import { Oscilloscope } from "./Oscilloscope";
import { Sparkline } from "./Sparkline";
import { sourceBitrate } from "./lib/icecast";
import { fmtMmSs } from "./lib/format";
import { loadWave, storeWave, WAVE_MODES, waveLabel, type WaveMode } from "./lib/wave";
import {
  cueDuration,
  elapsedSeconds,
  onAirCopy,
  pickNext,
  previousTrack,
  remainingSeconds,
  trackDuration,
  type OnAirCopy,
} from "./lib/now";
import type { HubSnap } from "./lib/poll";

type VoiceoverProps = {
  on: boolean;
  busy: boolean;
  error: string;
  fade: number;
  level: number;
  onToggle: () => void;
  onFade: (dir: -1 | 1) => void;
};

type Props = {
  snap: HubSnap;
  clock: string;
  since: string | null;
  on: boolean;
  starting: boolean;
  analyser: AnalyserNode | null;
  error: string;
  onToggle: () => void;
  voiceover: VoiceoverProps;
};

type NeighborProps = {
  label: string;
  copy: OnAirCopy;
  duration: number | null;
  ready: boolean;
  align: "start" | "end";
  fade: Transition;
  announce: boolean;
};

function Neighbor({ label, copy, duration, ready, align, fade, announce }: NeighborProps) {
  const line = copy.title;
  const artist = copy.artist;
  const tip = line && artist ? `${line} — ${artist}` : line || undefined;
  const empty = ready && !line;
  return (
    <div
      className={`scene-neighbor is-${align}${announce ? " is-annonce" : ""}${empty ? " is-empty" : ""}`}
    >
      <p className="scene-neighbor-label">
        <span>{label}</span>
        {ready && duration != null ? <span className="scene-neighbor-dur">{fmtMmSs(duration)}</span> : null}
      </p>
      <div className="scene-neighbor-slot">
        {ready ? (
          <AnimatePresence mode="wait">
            <motion.p
              key={line || "empty"}
              className="scene-neighbor-title"
              title={tip}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={fade}
            >
              {line || "—"}
            </motion.p>
          </AnimatePresence>
        ) : (
          <p className="skel skel-neighbor" />
        )}
      </div>
      <div className="scene-neighbor-slot is-artist">
        {!ready ? (
          <p className="skel skel-neighbor-artist" />
        ) : artist ? (
          <AnimatePresence mode="wait">
            <motion.p
              key={artist}
              className="scene-neighbor-artist"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={fade}
            >
              {artist}
            </motion.p>
          </AnimatePresence>
        ) : (
          <p className="scene-neighbor-artist">{"\u00a0"}</p>
        )}
      </div>
    </div>
  );
}

function remainLabel(seconds: number | null): string {
  if (seconds == null) return "";
  return fmtMmSs(seconds, "ceil");
}

function WaveMenu({ mode, onPick }: { mode: WaveMode; onPick: (next: WaveMode) => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(event: MouseEvent) {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={root} className={`wave-menu${open ? " is-open" : ""}`} onClick={(event) => event.stopPropagation()}>
      <button
        type="button"
        className="wave-menu-btn"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Rendu du casque"
        onClick={() => setOpen((value) => !value)}
      >
        {waveLabel(mode)}
      </button>
      {open ? (
        <ul className="wave-menu-list" role="listbox" aria-label="Waveform">
          {WAVE_MODES.map((row) => (
            <li key={row.id}>
              <button
                type="button"
                role="option"
                aria-selected={row.id === mode}
                className={row.id === mode ? "is-on" : undefined}
                onClick={() => {
                  onPick(row.id);
                  setOpen(false);
                }}
              >
                {row.label}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function Scene({
  snap,
  clock,
  since,
  on,
  starting,
  analyser,
  error,
  onToggle,
  voiceover,
}: Props) {
  const reduce = useReducedMotion();
  const fade = reduce ? { duration: 0 } : { duration: 0.4, ease: [0.22, 1, 0.36, 1] as const };
  const spring = reduce ? { duration: 0 } : { type: "spring" as const, stiffness: 420, damping: 32 };
  const { title, artist } = onAirCopy(snap.now, snap.nasgul);
  const prevCue = previousTrack(snap.now);
  const nextCue = pickNext(snap.now);
  const prev = onAirCopy(prevCue);
  const next = onAirCopy(nextCue);
  const bitrate = sourceBitrate(snap.nasgul);
  const label = on ? "Couper le casque" : "Casque studio";
  const listeners = snap.nasgul?.listeners ?? null;
  const peak = snap.nasgul?.listener_peak ?? null;
  const remain = remainingSeconds(snap.now);
  const elapsed = elapsedSeconds(snap.now);
  const duration = trackDuration(snap.now);
  const progress =
    elapsed != null && duration != null && duration > 0 ? Math.min(1, elapsed / duration) : null;
  const restore = voiceover.on && remain != null ? remainLabel(remain) : "";
  const [wave, setWave] = useState<WaveMode>(loadWave);

  return (
    <section className={`scene${voiceover.on ? " is-annonce" : ""}`} data-clock={clock}>
      <header className="scene-head">
        <div className="scene-trio">
          <Neighbor
            label="Précédent"
            copy={prev}
            duration={cueDuration(prevCue)}
            ready={snap.ready}
            align="start"
            fade={fade}
            announce={voiceover.on}
          />
          <div className={`scene-now${voiceover.on ? " is-annonce" : ""}`} aria-live="polite">
            {snap.ready ? (
              <>
                <p className={`scene-now-label${remain != null && remain <= 15 ? " is-ending" : ""}`}>
                  <span className="scene-now-kicker">
                    {voiceover.on ? <span className="live-dot" aria-hidden="true" /> : null}
                    {voiceover.on ? "Annonce" : "Maintenant"}
                  </span>
                  {remain != null ? (
                    <span className="scene-remain">−{fmtMmSs(remain, "ceil")}</span>
                  ) : elapsed != null ? (
                    <span className="scene-remain">{fmtMmSs(elapsed)}</span>
                  ) : null}
                </p>
                <div className="scene-title-slot">
                  <AnimatePresence mode="wait">
                    <motion.h1
                      key={title || "silent"}
                      className="scene-title"
                      initial={{ opacity: 0, y: 12 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -8 }}
                      transition={fade}
                    >
                      {title || "Silence"}
                    </motion.h1>
                  </AnimatePresence>
                </div>
                <div className="scene-artist-slot">
                  <AnimatePresence mode="wait">
                    <motion.p
                      key={artist || "none"}
                      className="scene-artist"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={fade}
                    >
                      {artist || "\u00a0"}
                    </motion.p>
                  </AnimatePresence>
                </div>
                {elapsed != null && duration != null ? (
                  <p className="scene-clock-span">
                    {fmtMmSs(elapsed)} / {fmtMmSs(duration)}
                  </p>
                ) : null}
                {progress != null ? (
                  <span className="scene-clock-bar" aria-hidden="true">
                    <i style={{ width: `${Math.round(progress * 1000) / 10}%` }} />
                  </span>
                ) : null}
              </>
            ) : (
              <>
                <p className="skel skel-badge" />
                <p className="skel skel-title" />
                <p className="skel skel-artist" />
              </>
            )}
          </div>
          <Neighbor
            label="Suivant"
            copy={next}
            duration={cueDuration(nextCue)}
            ready={snap.ready}
            align="end"
            fade={fade}
            announce={voiceover.on}
          />
        </div>
      </header>

      <div
        id="casque"
        className={`monitor${on ? " is-on" : ""}${starting ? " is-starting" : ""}${voiceover.on ? " is-stop" : ""}`}
      >
        {voiceover.on ? (
          <motion.button
            type="button"
            className="monitor-stop"
            onClick={voiceover.onToggle}
            disabled={voiceover.busy}
            aria-pressed
            aria-label="Stopper l’annonce"
            whileTap={reduce ? undefined : { scale: 0.987 }}
            transition={spring}
          >
            <span className="monitor-stop-sq" aria-hidden="true" />
            <span className="monitor-stop-label">{voiceover.busy ? "Calage" : "Stopper"}</span>
          </motion.button>
        ) : (
          <>
            <motion.button
              type="button"
              className="monitor-hit"
              onClick={onToggle}
              aria-pressed={on}
              aria-label={label}
              title={label}
              whileTap={reduce ? undefined : { scale: 0.987 }}
              transition={spring}
            >
              <Oscilloscope analyser={analyser} playing={on && !starting} mode={wave} className="monitor-wave" />
              {wave === "spectre" && on && !starting ? (
                <span className="monitor-bands" aria-hidden="true">
                  <span>B</span>
                  <span>M</span>
                  <span>A</span>
                </span>
              ) : null}
              <span className="monitor-hint">
                <SpeakerLoudIcon />
                <span>{starting ? "Calage" : on ? (error || "Casque") : "Casque"}</span>
              </span>
            </motion.button>
            <WaveMenu
              mode={wave}
              onPick={(next) => {
                setWave(storeWave(next));
              }}
            />
          </>
        )}
      </div>

      <div className="vo-bar">
        <div className={`vo-cta${voiceover.on ? " is-on" : ""}`}>
          <motion.button
            type="button"
            className="vo-hit"
            onClick={voiceover.onToggle}
            disabled={voiceover.busy}
            aria-pressed={voiceover.on}
            whileTap={reduce ? undefined : { scale: 0.987 }}
            transition={spring}
          >
            <span className="vo-meter" style={{ transform: `scaleX(${voiceover.on ? voiceover.level : 0})` }} />
            {voiceover.on ? <span className="live-dot" aria-hidden="true" /> : null}
            <span className="vo-label">{voiceover.busy ? "Calage" : voiceover.on ? "Remonter" : "Annonce"}</span>
          </motion.button>
          <div className="vo-fade">
            <span>Fondu</span>
            <button type="button" onClick={() => voiceover.onFade(-1)} aria-label="Fondu plus court">
              −
            </button>
            <b>{voiceover.fade.toFixed(1).replace(".", ",")} s</b>
            <button type="button" onClick={() => voiceover.onFade(1)} aria-label="Fondu plus long">
              +
            </button>
          </div>
        </div>
        <p className="vo-note">
          {voiceover.error ||
            (voiceover.on && restore ? `Remontée ${restore}` : "Micro Mac · casque")}
        </p>
      </div>

      <ul className="kpis">
        <li>
          <span>Auditeurs</span>
          <b>
            <Count value={listeners} ready={snap.ready} />
          </b>
        </li>
        <li>
          <span>Pic</span>
          <b>
            <Count value={peak} ready={snap.ready} />
          </b>
        </li>
        <li>
          <span>En onde</span>
          {snap.ready ? <b>{since || "à l’instant"}</b> : <b className="skel skel-num" />}
        </li>
        <li>
          <span>Débit</span>
          {snap.ready && bitrate != null ? (
            <b>
              <Count value={bitrate} ready />
              <small> kb/s</small>
            </b>
          ) : (
            <b className="skel skel-num" />
          )}
        </li>
      </ul>

      <div className="scene-spark">
        <span>Session</span>
        <Sparkline values={snap.listeners} ready={snap.ready} />
      </div>
    </section>
  );
}
