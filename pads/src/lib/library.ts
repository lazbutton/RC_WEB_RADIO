import { isPadColor, SEED_CATEGORIES } from "./padColor";
import { isAudioFile, titleFromFileName } from "./kind";
import type { CategoryId, LibraryCategory, LibraryFolder, LibrarySound, PadColor } from "../types";

const DB_NAME = "pads-library";
const SOUND_STORE = "sounds";
const FOLDER_STORE = "folders";
const CATEGORY_STORE = "categories";
const CACHE_STORE = "catalog-cache";

type StoredSound = LibrarySound & {
  mime: string;
  blob: Blob;
};

type CachedBlob = {
  soundId: string;
  mime: string;
  blob: Blob;
};

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 4);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(SOUND_STORE)) {
        req.result.createObjectStore(SOUND_STORE, { keyPath: "soundId" });
      }
      if (!req.result.objectStoreNames.contains(FOLDER_STORE)) {
        req.result.createObjectStore(FOLDER_STORE, { keyPath: "folderId" });
      }
      if (!req.result.objectStoreNames.contains(CATEGORY_STORE)) {
        req.result.createObjectStore(CATEGORY_STORE, { keyPath: "id" });
      }
      if (!req.result.objectStoreNames.contains(CACHE_STORE)) {
        req.result.createObjectStore(CACHE_STORE, { keyPath: "soundId" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("idb"));
  });
}

function reqOf<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("idb"));
  });
}

function readDuration(file: File): Promise<number> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const audio = document.createElement("audio");
    audio.preload = "metadata";
    const done = (value: number) => {
      URL.revokeObjectURL(url);
      resolve(value);
    };
    audio.onloadedmetadata = () => done(Number.isFinite(audio.duration) ? audio.duration : 0);
    audio.onerror = () => done(0);
    audio.src = url;
  });
}

function toMeta(sound: StoredSound): LibrarySound {
  return {
    soundId: sound.soundId,
    title: sound.title,
    durationSec: sound.durationSec,
    kind: sound.kind,
    color: sound.color,
  };
}

function parseCategory(row: unknown, fallbackOrder: number): LibraryCategory | null {
  if (!row || typeof row !== "object") return null;
  const rec = row as Record<string, unknown>;
  if (typeof rec.id !== "string" || !rec.id.trim()) return null;
  if (typeof rec.title !== "string") return null;
  const seeded = SEED_CATEGORIES.find((category) => category.id === rec.id);
  const order =
    typeof rec.order === "number" && Number.isFinite(rec.order)
      ? rec.order
      : (seeded?.order ?? fallbackOrder);
  return {
    id: rec.id,
    title: rec.title.trim() || rec.id,
    color: isPadColor(rec.color) ? rec.color : "amber",
    order,
  };
}

async function writeCategories(db: IDBDatabase, categories: LibraryCategory[]): Promise<void> {
  const tx = db.transaction(CATEGORY_STORE, "readwrite");
  for (const category of categories) {
    tx.objectStore(CATEGORY_STORE).put(category);
  }
  await new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("idb"));
  });
}

async function ensureCategories(db: IDBDatabase): Promise<LibraryCategory[]> {
  const existing = (
    await reqOf(db.transaction(CATEGORY_STORE, "readonly").objectStore(CATEGORY_STORE).getAll())
  )
    .map((row, index) => parseCategory(row, 10 + index))
    .filter((row): row is LibraryCategory => row != null);
  if (!existing.length) {
    await writeCategories(db, SEED_CATEGORIES);
    return [...SEED_CATEGORIES];
  }
  return [...existing].sort((a, b) => a.order - b.order || a.title.localeCompare(b.title, "fr"));
}

export async function listLibrary(): Promise<{
  sounds: LibrarySound[];
  folders: LibraryFolder[];
  categories: LibraryCategory[];
}> {
  const db = await openDb();
  const categories = await ensureCategories(db);
  const sounds = (
    await reqOf(db.transaction(SOUND_STORE, "readonly").objectStore(SOUND_STORE).getAll() as IDBRequest<StoredSound[]>)
  ).map(toMeta);
  const folders = (
    await reqOf(db.transaction(FOLDER_STORE, "readonly").objectStore(FOLDER_STORE).getAll() as IDBRequest<LibraryFolder[]>)
  ).map((folder) => ({
    ...folder,
    soundIds: folder.soundIds.filter((id) => sounds.some((sound) => sound.soundId === id)),
  }));
  return { sounds, folders, categories };
}

export async function putCategory(category: LibraryCategory): Promise<void> {
  const db = await openDb();
  await reqOf(db.transaction(CATEGORY_STORE, "readwrite").objectStore(CATEGORY_STORE).put(category));
}

export async function deleteCategory(id: CategoryId): Promise<void> {
  const db = await openDb();
  await reqOf(db.transaction(CATEGORY_STORE, "readwrite").objectStore(CATEGORY_STORE).delete(id));
}

export async function getSoundBlob(soundId: string): Promise<Blob | null> {
  const db = await openDb();
  const row = await reqOf(
    db.transaction(SOUND_STORE, "readonly").objectStore(SOUND_STORE).get(soundId) as IDBRequest<StoredSound | undefined>,
  );
  if (row?.blob) return row.blob;
  const cached = await reqOf(
    db.transaction(CACHE_STORE, "readonly").objectStore(CACHE_STORE).get(soundId) as IDBRequest<CachedBlob | undefined>,
  );
  return cached?.blob ?? null;
}

export async function cacheCatalogBlob(soundId: string, blob: Blob, mime = "audio/mpeg"): Promise<void> {
  const db = await openDb();
  const row: CachedBlob = { soundId, blob, mime };
  await reqOf(db.transaction(CACHE_STORE, "readwrite").objectStore(CACHE_STORE).put(row));
}

export async function importAudioFiles(
  files: File[],
  kind: CategoryId,
  color: PadColor,
): Promise<LibrarySound[]> {
  const db = await openDb();
  const added: LibrarySound[] = [];
  for (const file of files) {
    if (!isAudioFile(file)) continue;
    const durationSec = await readDuration(file);
    const sound: StoredSound = {
      soundId: crypto.randomUUID(),
      title: titleFromFileName(file.name),
      durationSec,
      kind,
      color,
      mime: file.type || "audio/mpeg",
      blob: file,
    };
    await reqOf(db.transaction(SOUND_STORE, "readwrite").objectStore(SOUND_STORE).put(sound));
    added.push(toMeta(sound));
  }
  return added;
}

export async function importFolder(
  files: File[],
  kind: CategoryId,
  title: string,
  color: PadColor,
): Promise<{ folder: LibraryFolder | null; sounds: LibrarySound[] }> {
  const sounds = await importAudioFiles(files, kind, color);
  if (!sounds.length) return { folder: null, sounds };
  const folder: LibraryFolder = {
    folderId: crypto.randomUUID(),
    title: title.trim() || "Dossier",
    kind,
    color,
    soundIds: sounds.map((sound) => sound.soundId),
  };
  const db = await openDb();
  await reqOf(db.transaction(FOLDER_STORE, "readwrite").objectStore(FOLDER_STORE).put(folder));
  return { folder, sounds };
}

export async function recategorizeSound(
  soundId: string,
  kind: CategoryId,
  color: PadColor,
): Promise<LibrarySound | null> {
  const db = await openDb();
  const tx = db.transaction(SOUND_STORE, "readwrite");
  const store = tx.objectStore(SOUND_STORE);
  const row = await reqOf(store.get(soundId) as IDBRequest<StoredSound | undefined>);
  if (!row) return null;
  const next: StoredSound = { ...row, kind, color };
  await reqOf(store.put(next));
  return toMeta(next);
}

export async function recategorizeFolder(
  folderId: string,
  kind: CategoryId,
  color: PadColor,
): Promise<{ folder: LibraryFolder; soundIds: string[] } | null> {
  const db = await openDb();
  const folder = await reqOf(
    db.transaction(FOLDER_STORE, "readonly").objectStore(FOLDER_STORE).get(folderId) as IDBRequest<
      LibraryFolder | undefined
    >,
  );
  if (!folder) return null;
  const nextFolder: LibraryFolder = { ...folder, kind, color };
  await reqOf(db.transaction(FOLDER_STORE, "readwrite").objectStore(FOLDER_STORE).put(nextFolder));
  for (const soundId of folder.soundIds) {
    await recategorizeSound(soundId, kind, color);
  }
  return { folder: nextFolder, soundIds: folder.soundIds };
}

export async function deleteSound(soundId: string): Promise<void> {
  const db = await openDb();
  await reqOf(db.transaction(SOUND_STORE, "readwrite").objectStore(SOUND_STORE).delete(soundId));
  const folders = await reqOf(
    db.transaction(FOLDER_STORE, "readonly").objectStore(FOLDER_STORE).getAll() as IDBRequest<LibraryFolder[]>,
  );
  for (const folder of folders) {
    if (!folder.soundIds.includes(soundId)) continue;
    const nextIds = folder.soundIds.filter((id) => id !== soundId);
    const tx = db.transaction(FOLDER_STORE, "readwrite");
    if (nextIds.length) {
      await reqOf(tx.objectStore(FOLDER_STORE).put({ ...folder, soundIds: nextIds }));
    } else {
      await reqOf(tx.objectStore(FOLDER_STORE).delete(folder.folderId));
    }
  }
}

export async function deleteFolder(folderId: string): Promise<string[]> {
  const db = await openDb();
  const folder = await reqOf(
    db.transaction(FOLDER_STORE, "readonly").objectStore(FOLDER_STORE).get(folderId) as IDBRequest<LibraryFolder | undefined>,
  );
  const soundIds = folder?.soundIds ?? [];
  await reqOf(db.transaction(FOLDER_STORE, "readwrite").objectStore(FOLDER_STORE).delete(folderId));
  for (const soundId of soundIds) {
    await reqOf(db.transaction(SOUND_STORE, "readwrite").objectStore(SOUND_STORE).delete(soundId));
  }
  return soundIds;
}
