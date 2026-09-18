import { useState, type DragEvent, type MouseEvent } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Shuffle } from "lucide-react";
import { formatMmSs, splitLabel } from "../lib/format";
import { collectDroppedFiles } from "../lib/dropFiles";
import { noteAt } from "../midi/apcMiniMk2";
import { useSurfaceDrag } from "../drag/surfaceDrag";
import type { LibraryCategory, LibraryFolder, PadAssign, PadColor, PadMap } from "../types";
import { ColorMenu } from "./ColorMenu";

function hasFiles(event: DragEvent): boolean {
  return [...event.dataTransfer.types].includes("Files");
}

export function PadGrid({
  map,
  folders,
  categories,
  firedNote,
  onAirNote,
  remaining,
  duration,
  endingSoon,
  armedNotes,
  locked,
  onFire,
  onColor,
  onMedia,
}: {
  map: PadMap;
  folders: LibraryFolder[];
  categories: LibraryCategory[];
  firedNote: number | null;
  onAirNote: number | null;
  remaining: number | null;
  duration: number | null;
  endingSoon: boolean;
  armedNotes: Set<number>;
  locked?: boolean;
  onFire: (note: number) => void;
  onColor: (note: number, color: PadColor) => void;
  onMedia: (note: number, files: File[], folderName: string | null) => void;
}) {
  const cells: number[] = [];
  for (let row = 7; row >= 0; row--) {
    for (let col = 0; col < 8; col++) cells.push(noteAt(row, col));
  }
  const [menu, setMenu] = useState<{ note: number; x: number; y: number } | null>(null);
  const menuSlot = menu ? map[menu.note] : null;

  return (
    <>
      <div className="grid" role="grid" aria-label="Pads 8 par 8">
        {cells.map((note) => {
          const slot = map[note] ?? null;
          const count =
            slot?.mode === "folder"
              ? (folders.find((folder) => folder.folderId === slot.folderId)?.soundIds.length ?? 0)
              : 0;
          return (
          <PadCell
            key={note}
            note={note}
            slot={slot}
            categoryTitle={categories.find((row) => row.id === slot?.kind)?.title ?? slot?.kind ?? ""}
            count={count}
            fired={firedNote === note}
            onAir={onAirNote === note}
            remaining={onAirNote === note ? remaining : null}
            duration={onAirNote === note ? duration : null}
            endingSoon={endingSoon && onAirNote === note}
            armed={armedNotes.has(note)}
            locked={Boolean(locked)}
            onFire={onFire}
            onMedia={onMedia}
            onColorMenu={(event) => {
              event.preventDefault();
              const slot = map[note];
              if (!slot) return;
              setMenu({ note, x: event.clientX, y: event.clientY });
            }}
          />
          );
        })}
      </div>
      {menu && menuSlot ? (
        <ColorMenu
          x={menu.x}
          y={menu.y}
          current={menuSlot.color}
          onPick={(color) => {
            onColor(menu.note, color);
            setMenu(null);
          }}
          onClose={() => setMenu(null)}
        />
      ) : null}
    </>
  );
}

function PadCell({
  note,
  slot,
  categoryTitle,
  count,
  fired,
  onAir,
  remaining,
  duration,
  endingSoon,
  armed,
  locked,
  onFire,
  onMedia,
  onColorMenu,
}: {
  note: number;
  slot: PadAssign | null;
  categoryTitle: string;
  count: number;
  fired: boolean;
  onAir: boolean;
  remaining: number | null;
  duration: number | null;
  endingSoon: boolean;
  armed: boolean;
  locked: boolean;
  onFire: (note: number) => void;
  onMedia: (note: number, files: File[], folderName: string | null) => void;
  onColorMenu: (event: MouseEvent<HTMLElement>) => void;
}) {
  const reduce = useReducedMotion();
  const { begin, session, blockClick } = useSurfaceDrag();
  const over = !locked && session?.overNote === note;
  const origin = !locked && session?.source.type === "slot" && session.source.note === note;
  const folder = slot?.mode === "folder";
  const progress =
    onAir && remaining != null && duration != null && duration > 0
      ? Math.min(1, Math.max(0, 1 - remaining / duration))
      : null;

  return (
    <div
      className={`pad-drop${over ? " is-over" : ""}`}
      data-pad-note={note}
      role="gridcell"
      onDragEnter={(event) => {
        if (!locked && hasFiles(event)) event.preventDefault();
      }}
      onDragOver={(event) => {
        if (!hasFiles(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = locked ? "none" : "copy";
      }}
      onDrop={(event) => {
        if (!hasFiles(event)) return;
        event.preventDefault();
        if (locked) return;
        void collectDroppedFiles(event).then(({ files, folderName }) => onMedia(note, files, folderName));
      }}
    >
      <motion.button
        type="button"
        className={[
          "pad",
          slot ? "" : "is-empty",
          folder ? "is-folder" : "",
          fired ? "is-fired" : "",
          onAir ? "is-on-air" : "",
          endingSoon ? "is-ending" : "",
          armed ? "is-armed" : "",
          origin ? "is-dragging" : "",
          locked ? "is-live" : "",
        ]
          .filter(Boolean)
          .join(" ")}
        data-color={slot?.color}
        aria-label={
          slot
            ? [
                folder
                  ? `Pad ${note + 1} dossier ${slot.title}, ${count} sons aléatoires`
                  : `Pad ${note + 1} ${slot.title}`,
                onAir && remaining != null ? `reste ${formatMmSs(Math.floor(remaining))}` : "",
              ]
                .filter(Boolean)
                .join(" — ")
            : locked
              ? `Pad ${note + 1} vide`
              : `Pad ${note + 1} vide — déposer un son ou un dossier`
        }
        onPointerDown={(event) => {
          if (locked || !slot) return;
          begin({ type: "slot", note, assign: slot }, event);
        }}
        onClick={() => {
          if (blockClick()) return;
          if (slot) onFire(note);
        }}
        onContextMenu={onColorMenu}
        whileTap={reduce || !slot ? undefined : { scale: 0.97 }}
        transition={{ duration: reduce ? 0 : 0.08, ease: [0.22, 1, 0.36, 1] }}
      >
        {slot ? (
          <PadLabel
            slot={slot}
            categoryTitle={categoryTitle}
            count={count}
            remaining={remaining}
            onAir={onAir}
            reduce={Boolean(reduce)}
          />
        ) : (
          <span className="pad-empty" aria-hidden="true">
            {note}
          </span>
        )}
        {progress != null ? (
          <span className="pad-progress" style={{ transform: `scaleX(${progress})` }} aria-hidden="true" />
        ) : null}
        {onAir ? <span className="pad-ring" aria-hidden="true" /> : null}
        {fired ? <span className="pad-flash" aria-hidden="true" /> : null}
      </motion.button>
    </div>
  );
}

function PadLabel({
  slot,
  categoryTitle,
  count,
  remaining,
  onAir,
  reduce,
}: {
  slot: PadAssign;
  categoryTitle: string;
  count: number;
  remaining: number | null;
  onAir: boolean;
  reduce: boolean;
}) {
  const folder = slot.mode === "folder";
  const { lead, rest } = folder ? { lead: slot.title, rest: null } : splitLabel(slot.title);
  const clockSec =
    onAir && remaining != null
      ? Math.max(0, Math.floor(remaining))
      : folder
        ? null
        : Math.max(0, Math.round(slot.durationSec));

  return (
    <motion.span
      className={`pad-copy${onAir ? " is-playing" : ""}${clockSec != null ? " has-clock" : ""}`}
      initial={reduce ? false : { opacity: 0, filter: "blur(6px)" }}
      animate={{ opacity: 1, filter: "blur(0px)" }}
      transition={{ duration: reduce ? 0 : 0.22 }}
    >
      <span className="pad-title">{folder ? slot.title : (rest ?? lead)}</span>
      {clockSec != null ? (
        <span className="pad-clock" aria-hidden="true">
          <motion.span
            key={onAir ? clockSec : "idle"}
            className="pad-clock-tick"
            initial={reduce || !onAir ? false : { scale: 1.12, opacity: 0.55 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: reduce || !onAir ? 0 : 0.2, ease: [0.22, 1, 0.36, 1] }}
          >
            {formatMmSs(clockSec)}
          </motion.span>
        </span>
      ) : null}
      <span className="pad-meta">
        <span className="pad-lead">{folder ? "aléatoire" : rest ? lead : categoryTitle}</span>
        <span className="pad-dur">
          {folder ? (
            <>
              <Shuffle size={10} aria-hidden="true" />
              {count}
            </>
          ) : (
            formatMmSs(slot.durationSec)
          )}
        </span>
      </span>
    </motion.span>
  );
}
