/** Akai APC Mini MK2 — Session / Normal. Notes 0–63, bas-gauche = 0. */

import { apcVelocity, JINGLE_ID } from "../lib/padColor";
import type { PadMap } from "../types";

export const SCENE_SKIP = 112;
export const SCENE_CHAIN = 113;
export const SHIFT_NOTE = 122;

export const APC_COLOR = {
  off: 0,
  white: 3,
} as const;

const LED_SOLID_FULL = 0x96;
const LED_PULSE = 0x98;
const LED_BUTTON = 0x90;

export const SYSEX_INTRO = [
  0xf0, 0x47, 0x7f, 0x4f, 0x60, 0x00, 0x04, 0x00, 0x01, 0x00, 0x00, 0xf7,
];
export const SYSEX_NORMAL = [0xf0, 0x47, 0x7f, 0x4f, 0x62, 0x00, 0x01, 0x00, 0xf7];

export type ApcLedState = {
  map: PadMap;
  playingNote: number | null;
  playingLit: boolean;
  jingleArmed: boolean;
  chainAfterSound: boolean;
};

export function isApcMiniMk2Name(name: string | undefined | null): boolean {
  const n = (name ?? "").toLowerCase();
  if (!n.includes("apc") || !n.includes("mini")) return false;
  return /mk\s*2/.test(n) || n.includes("mkii") || n.includes("mk2");
}

export function noteAt(row: number, col: number): number {
  return row * 8 + col;
}

export function sendAll(outputs: MIDIOutput[], data: number[]): void {
  for (const out of outputs) {
    try {
      out.send(data);
    } catch {
      /* port fermé */
    }
  }
}

export function initApc(outputs: MIDIOutput[], sysex: boolean): void {
  if (sysex) {
    sendAll(outputs, SYSEX_INTRO);
    sendAll(outputs, SYSEX_NORMAL);
  }
}

export function clearApc(outputs: MIDIOutput[]): void {
  for (let note = 0; note < 64; note++) {
    sendAll(outputs, [LED_SOLID_FULL, note, 0]);
  }
  for (let n = 100; n <= 107; n++) sendAll(outputs, [LED_BUTTON, n, 0]);
  for (let n = 112; n <= 119; n++) sendAll(outputs, [LED_BUTTON, n, 0]);
}

export function paintApc(outputs: MIDIOutput[], state: ApcLedState): void {
  if (!outputs.length) return;

  for (let note = 0; note < 64; note++) {
    const slot = state.map[note];
    if (!slot) {
      sendAll(outputs, [LED_SOLID_FULL, note, 0]);
      continue;
    }
    const color = apcVelocity(slot.color);
    const isPlaying = state.playingNote === note;
    const armed = state.jingleArmed && slot.kind === JINGLE_ID;
    if (isPlaying) {
      sendAll(outputs, [LED_SOLID_FULL, note, state.playingLit ? APC_COLOR.white : 0]);
    } else if (armed) sendAll(outputs, [LED_PULSE, note, color]);
    else sendAll(outputs, [LED_SOLID_FULL, note, color]);
  }

  for (let n = 100; n <= 107; n++) sendAll(outputs, [LED_BUTTON, n, 0]);
  sendAll(outputs, [LED_BUTTON, SCENE_SKIP, 1]);
  sendAll(outputs, [
    LED_BUTTON,
    SCENE_CHAIN,
    state.chainAfterSound ? (state.jingleArmed ? 2 : 1) : 0,
  ]);
}
