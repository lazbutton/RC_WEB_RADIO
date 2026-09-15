import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect } from "react";
import { Oscilloscope } from "./Oscilloscope";
import { PlayButton } from "./PlayButton";
import { formatRemain, useRadio } from "./useRadio";

export function App() {
  const { audioRef, playing, analyser, now, remaining, toggle } = useRadio();
  const reduce = useReducedMotion();
  const fade = reduce ? { duration: 0 } : { duration: 0.35, ease: [0.22, 1, 0.36, 1] as const };

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.code !== "Space" && e.key !== " ") return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "BUTTON") return;
      e.preventDefault();
      toggle();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);

  return (
    <main className="embed">
      <section className="now" aria-live="polite">
        <div className="now-title-slot">
          <AnimatePresence mode="wait">
            <motion.h1
              key={now.title || "silent"}
              className="now-title"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={fade}
            >
              {now.title}
            </motion.h1>
          </AnimatePresence>
        </div>
        <div className="now-meta">
          <div className="now-artist-slot">
            <AnimatePresence mode="wait">
              <motion.p
                key={now.artist || "none"}
                className="now-artist"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={fade}
              >
                {now.artist}
              </motion.p>
            </AnimatePresence>
          </div>
          {remaining !== null ? (
            <p className="now-remain" aria-label={`Temps restant ${formatRemain(remaining)}`}>
              {formatRemain(remaining)}
            </p>
          ) : null}
        </div>
      </section>

      <div className="row">
        <PlayButton playing={playing} onToggle={toggle} />
        <div className="scope">
          <Oscilloscope analyser={analyser} playing={playing} />
        </div>
      </div>

      <audio ref={audioRef} preload="none" />
    </main>
  );
}
