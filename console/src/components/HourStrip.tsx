import { Tooltip } from "@radix-ui/themes";

export type StudioSelection =
  | { kind: "idle" }
  | { kind: "fill"; zone: "0-20" | "20-40" | "40-60" }
  | { kind: "anchor"; minute: 20 | 40 };

type HourStripProps = {
  selection: StudioSelection;
  onSelect: (next: StudioSelection) => void;
};

const FILLS: { zone: "0-20" | "20-40" | "40-60"; top: string; height: string }[] = [
  { zone: "0-20", top: "0%", height: "33.333%" },
  { zone: "20-40", top: "33.333%", height: "33.333%" },
  { zone: "40-60", top: "66.666%", height: "33.334%" },
];

const MARKS = ["00", "10", "20", "30", "40", "50", "60"] as const;
const MAJOR = new Set(["00", "20", "40", "60"]);

export function HourStrip({ selection, onSelect }: HourStripProps) {
  return (
    <div className="hour-strip" role="img" aria-label="Heure type, 0 à 60 minutes">
      {MARKS.map((label) => (
        <span
          key={label}
          className={`hour-label${MAJOR.has(label) ? " is-major" : ""}`}
          style={{ top: `${(Number(label) / 60) * 100}%` }}
        >
          :{label}
        </span>
      ))}

      {FILLS.map((fill) => {
        const selected = selection.kind === "fill" && selection.zone === fill.zone;
        return (
          <Tooltip key={fill.zone} content="Zone de remplissage — motif en boucle">
            <button
              type="button"
              className={`hour-fill${selected ? " is-selected" : ""}`}
              style={{ top: fill.top, height: fill.height }}
              aria-pressed={selected}
              aria-label={`Zone de motif ${fill.zone}`}
              onClick={() => onSelect({ kind: "fill", zone: fill.zone })}
            >
              Motif
            </button>
          </Tooltip>
        );
      })}

      {([20, 40] as const).map((minute) => {
        const selected = selection.kind === "anchor" && selection.minute === minute;
        return (
          <div
            key={minute}
            className={`hour-anchor${selected ? " is-selected" : ""}`}
            style={{ top: `${(minute / 60) * 100}%` }}
          >
            <Tooltip content="Rail d’ancre vide (pub / son) — sans cart">
              <button
                type="button"
                aria-pressed={selected}
                aria-label={`Ancre vide à :${minute}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect({ kind: "anchor", minute });
                }}
              >
                <span className="hour-anchor-line" />
              </button>
            </Tooltip>
          </div>
        );
      })}
    </div>
  );
}
