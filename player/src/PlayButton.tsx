import { motion, useReducedMotion } from "motion/react";

type Props = {
  playing: boolean;
  onToggle: () => void;
};

export function PlayButton({ playing, onToggle }: Props) {
  const reduce = useReducedMotion();
  return (
    <button
      type="button"
      id="play"
      className={playing ? "play-btn is-live" : "play-btn"}
      aria-pressed={playing}
      aria-label={playing ? "Arrêter BUTTON" : "Écouter BUTTON"}
      onClick={onToggle}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <motion.path
          fill="currentColor"
          animate={{
            d: playing ? "M6 6h12v12H6z" : "M8 5.5v13l11-6.5z",
          }}
          transition={
            reduce ? { duration: 0 } : { duration: 0.32, ease: [0.22, 1, 0.36, 1] }
          }
        />
      </svg>
    </button>
  );
}
