import { colorFromKind } from "./padColor";
import type { LibraryFolder, LibrarySound, PadAssign } from "../types";

export function toAssign(sound: LibrarySound): PadAssign {
  return {
    mode: "one",
    soundId: sound.soundId,
    title: sound.title,
    durationSec: sound.durationSec,
    kind: sound.kind,
    color: sound.color ?? colorFromKind(sound.kind),
    ...(sound.catalogId ? { catalogId: sound.catalogId } : {}),
    ...(sound.source ? { source: sound.source } : {}),
  };
}

export function toFolderAssign(folder: LibraryFolder): PadAssign {
  return {
    mode: "folder",
    folderId: folder.folderId,
    title: folder.title,
    kind: folder.kind,
    color: folder.color ?? colorFromKind(folder.kind),
  };
}

export function titleFromFileName(name: string): string {
  return name.replace(/\.[^.]+$/, "").replace(/[_]+/g, " ").trim() || name;
}

export function isAudioFile(file: File): boolean {
  if (file.type.startsWith("audio/")) return true;
  return /\.(mp3|wav|flac|ogg|m4a|aac|aiff|aif|webm)$/i.test(file.name);
}
