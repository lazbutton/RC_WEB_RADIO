import { Badge, IconButton, Tooltip } from "@radix-ui/themes";
import { Cross2Icon, LockClosedIcon } from "@radix-ui/react-icons";
import { useLayoutEffect, useRef, useState } from "react";
import { formatMmSs } from "../lib/parisClock";
import { itemLabel, kindLabel } from "../mock/catalog";
import type { QueueItem } from "../mock/buildQueue";
import { TrackCopy } from "./TrackCopy";

const PLAYED_IN_VIEW = 5;

export function QueueEditList({
  items,
  mode,
  nowUid,
  nextUid,
  playedUids,
  onMove,
  onRemove,
}: {
  items: QueueItem[];
  mode: "upcoming" | "window";
  nowUid?: string;
  nextUid?: string;
  playedUids?: ReadonlySet<string>;
  onMove: (fromUid: string, toUid: string) => void;
  onRemove: (uid: string) => void;
}) {
  const [dragUid, setDragUid] = useState<string | null>(null);
  const [overUid, setOverUid] = useState<string | null>(null);
  const listRef = useRef<HTMLOListElement | null>(null);
  const pinRef = useRef<HTMLLIElement | null>(null);

  const nowIndex = mode === "window" ? items.findIndex((row) => row.uid === nowUid) : -1;
  const pinIndex = nowIndex < 0 ? 0 : Math.max(0, nowIndex - PLAYED_IN_VIEW);

  useLayoutEffect(() => {
    if (mode !== "window") return;
    const list = listRef.current;
    const pin = pinRef.current;
    if (!list || !pin) return;
    list.scrollTop += pin.getBoundingClientRect().top - list.getBoundingClientRect().top;
  }, [mode, nowUid, items.length, pinIndex]);

  return (
    <ol
      ref={mode === "window" ? listRef : undefined}
      className={mode === "upcoming" ? "desk-upcoming" : "conductor-log"}
      aria-label={mode === "upcoming" ? "Dix suivants" : "Conducteur : passés et à venir"}
    >
      {items.map((row, i) => {
        const isPlayed = mode === "window" && (playedUids?.has(row.uid) ?? false);
        const isNow = mode === "window" ? row.uid === nowUid : false;
        const isNext =
          mode === "upcoming" ? i === 0 : row.uid === nextUid;
        const locked = isNow || isPlayed || row.role === "anchor";
        const droppable = !isNow && !isPlayed;
        const mark = isNow ? "Now" : isNext ? "Next" : null;
        const lockHint = isNow ? "Now — en cours" : isPlayed ? "Déjà joué" : "Ancre dure";

        return (
          <li
            key={row.uid}
            ref={i === pinIndex ? pinRef : undefined}
            className={`is-${row.item.kind}${isNow ? " is-now" : ""}${isNext ? " is-next" : ""}${
              isPlayed ? " is-played" : ""
            }${overUid === row.uid ? " is-drop" : ""}${dragUid === row.uid ? " is-drag" : ""}${
              locked ? "" : " is-draggable"
            }`}
            aria-label={`${isPlayed ? "Joué" : isNow ? "Now" : isNext ? "Next" : i + (mode === "upcoming" ? 1 : 0)} ${itemLabel(row.item)}`}
            draggable={!locked}
            onDragStart={(event) => {
              if (locked || (event.target as HTMLElement).closest("button")) {
                event.preventDefault();
                return;
              }
              event.dataTransfer.effectAllowed = "move";
              event.dataTransfer.setData("text/plain", row.uid);
              setDragUid(row.uid);
            }}
            onDragEnd={() => {
              setDragUid(null);
              setOverUid(null);
            }}
            onDragOver={(event) => {
              if (!droppable) return;
              event.preventDefault();
              event.dataTransfer.dropEffect = "move";
              if (dragUid !== row.uid) setOverUid(row.uid);
            }}
            onDragLeave={() => {
              if (overUid === row.uid) setOverUid(null);
            }}
            onDrop={(event) => {
              event.preventDefault();
              const from = event.dataTransfer.getData("text/plain") || dragUid;
              setOverUid(null);
              setDragUid(null);
              if (!from || from === row.uid || !droppable) return;
              onMove(from, row.uid);
            }}
          >
            {mode === "upcoming" ? (
              <span className="desk-upcoming-n">{isNext ? "N" : i + 1}</span>
            ) : (
              <span
                className={`kind-dot is-${row.item.kind}`}
                title={kindLabel(row.item.kind)}
                aria-hidden="true"
              />
            )}
            {mode === "upcoming" ? (
              <span
                className={`kind-dot is-${row.item.kind}`}
                title={kindLabel(row.item.kind)}
                aria-hidden="true"
              />
            ) : mark ? (
              <Badge size="1" variant="solid" color={isNow ? "red" : "gray"}>
                {mark}
              </Badge>
            ) : (
              <span className="conductor-log-mark" />
            )}
            {mode === "window" ? (
              <span className="conductor-log-anchor">
                {row.anchor ? (
                  <Tooltip content="Ancre dure">
                    <Badge size="1" color="red" variant="soft">
                      {row.anchor}
                    </Badge>
                  </Tooltip>
                ) : null}
              </span>
            ) : null}
            <TrackCopy item={row.item} />
            <span className="desk-upcoming-dur">{formatMmSs(row.item.durationSec)}</span>
            <span className="queue-edit-actions">
              {locked ? (
                <Tooltip content={lockHint}>
                  <span className="queue-edit-lock" aria-label={lockHint}>
                    <LockClosedIcon />
                  </span>
                </Tooltip>
              ) : (
                <Tooltip content="Retirer de la file">
                  <IconButton
                    size="1"
                    variant="ghost"
                    color="gray"
                    aria-label={`Retirer ${itemLabel(row.item)}`}
                    onClick={() => onRemove(row.uid)}
                  >
                    <Cross2Icon />
                  </IconButton>
                </Tooltip>
              )}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
