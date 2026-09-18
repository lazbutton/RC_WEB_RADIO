import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { forgetBuffer, isNotePlaying, playSound, snapshot, stopAll, subscribe, unlockAudio } from "./audio/engine";
import { PadGrid } from "./components/PadGrid";
import { SoundRail } from "./components/SoundRail";
import { TopBar, type UiMode } from "./components/TopBar";
import { SurfaceDrag, type DragSource } from "./drag/surfaceDrag";
import { toAssign, toFolderAssign } from "./lib/kind";
import { deleteCategory, deleteFolder, deleteSound, getSoundBlob, importAudioFiles, importFolder, listLibrary, putCategory, recategorizeFolder, recategorizeSound } from "./lib/library";
import { ensureCatalogCache, hitToSound, searchCatalog } from "./lib/catalog";
import { loadMap, saveMap } from "./lib/mapStore";
import { colorFromCategory, JINGLE_ID, MUSIC_ID, nextCategoryColor } from "./lib/padColor";
import { pickRandomExcept } from "./lib/random";
import { useApcMini } from "./midi/useApcMini";
import type { CategoryId, LibraryCategory, LibraryFolder, LibrarySound, PadAssign, PadColor, PadMap } from "./types";

const MODE_KEY = "pads-ui-mode";

function loadMode(): UiMode {
  try {
    return window.localStorage.getItem(MODE_KEY) === "edit" ? "edit" : "live";
  } catch {
    return "live";
  }
}

export function App() {
  const [sounds, setSounds] = useState<LibrarySound[]>([]);
  const [remoteSounds, setRemoteSounds] = useState<LibrarySound[]>([]);
  const [folders, setFolders] = useState<LibraryFolder[]>([]);
  const [categories, setCategories] = useState<LibraryCategory[]>([]);
  const [map, setMap] = useState<PadMap>(() => loadMap());
  const [chainAfterSound, setChainAfterSound] = useState(true);
  const [jingleArmed, setJingleArmed] = useState(false);
  const [firedNote, setFiredNote] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [now, setNow] = useState(() => snapshot());
  const [importing, setImporting] = useState(false);
  const [mode, setMode] = useState<UiMode>(loadMode);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const lastPlayed = useRef<Record<number, string>>({});
  const lastImportKind = useRef<CategoryId | null>(null);
  const firing = useRef(new Set<number>());
  const categoriesRef = useRef(categories);
  categoriesRef.current = categories;

  const librarySounds = useMemo(() => {
    const local = new Set(sounds.map((sound) => sound.soundId));
    return [...sounds, ...remoteSounds.filter((sound) => !local.has(sound.soundId))];
  }, [remoteSounds, sounds]);

  const loadCatalog = useCallback(async (query: string, kind: CategoryId | null) => {
    try {
      const hits = await searchCatalog({
        q: query.trim() || undefined,
        kind: kind || undefined,
        limit: 80,
      });
      setRemoteSounds(hits.map(hitToSound));
    } catch {
      setRemoteSounds([]);
    }
  }, []);

  useEffect(() => {
    void listLibrary().then((library) => {
      setSounds(library.sounds);
      setFolders(library.folders);
      setCategories(library.categories);
    });
  }, []);

  useEffect(() => {
    saveMap(map);
    for (const slot of map) {
      if (slot?.mode === "one" && slot.catalogId) {
        void ensureCatalogCache(slot.soundId, slot.catalogId);
      }
    }
  }, [map]);

  useEffect(() => {
    try {
      window.localStorage.setItem(MODE_KEY, mode);
    } catch {
      /* quota */
    }
    if (mode === "edit") setLibraryOpen(false);
  }, [mode]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setLibraryOpen(false);
        return;
      }
      const typing = (event.target as HTMLElement | null)?.closest("input, textarea, select");
      if (typing) return;
      if (event.key === "/" && mode === "live") {
        event.preventDefault();
        setLibraryOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode]);

  useEffect(() => {
    const unlock = () => {
      void unlockAudio();
    };
    window.addEventListener("pointerdown", unlock, { once: true });
    return () => window.removeEventListener("pointerdown", unlock);
  }, []);

  useEffect(() => subscribe(setNow), []);

  useEffect(() => {
    if (!now) return;
    const timer = window.setInterval(() => setNow(snapshot()), 200);
    return () => window.clearInterval(timer);
  }, [now?.note, now?.title]);

  const show = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 2200);
  };

  const pulse = (note: number) => {
    setFiredNote(note);
    window.setTimeout(() => setFiredNote((current) => (current === note ? null : current)), 140);
  };

  const ingest = useCallback(
    async (
      files: File[],
      kind: CategoryId,
      options: { note?: number; folderName?: string | null } = {},
    ) => {
      if (!files.length) return;
      const { note, folderName } = options;
      const asFolder = Boolean(folderName) || (note != null && files.length > 1);
      const color = colorFromCategory(categories, kind);
      lastImportKind.current = kind;
      setImporting(true);
      try {
        if (asFolder) {
          const { folder, sounds: added } = await importFolder(files, kind, folderName ?? "Dossier", color);
          if (!folder || !added.length) {
            show("Aucun fichier audio.");
            return;
          }
          setSounds((prev) => [...added, ...prev]);
          setFolders((prev) => [folder, ...prev]);
          if (note != null) {
            delete lastPlayed.current[note];
            setMap((prev) => {
              const next = [...prev];
              next[note] = toFolderAssign(folder);
              return next;
            });
          }
          return;
        }
        const added = await importAudioFiles(files, kind, color);
        if (!added.length) {
          show("Aucun fichier audio.");
          return;
        }
        setSounds((prev) => [...added, ...prev]);
        if (note != null) {
          delete lastPlayed.current[note];
          setMap((prev) => {
            const next = [...prev];
            next[note] = toAssign(added[0]);
            return next;
          });
        }
      } catch {
        show("Import impossible.");
      } finally {
        setImporting(false);
      }
    },
    [categories],
  );

  const removeSound = useCallback(async (soundId: string) => {
    if (!soundId.startsWith("ntr:")) {
      await deleteSound(soundId);
    }
    forgetBuffer(soundId);
    setSounds((prev) => prev.filter((sound) => sound.soundId !== soundId));
    setFolders((prev) =>
      prev
        .map((folder) => ({ ...folder, soundIds: folder.soundIds.filter((id) => id !== soundId) }))
        .filter((folder) => folder.soundIds.length > 0),
    );
    setMap((prev) =>
      prev.map((slot) => (slot?.mode === "one" && slot.soundId === soundId ? null : slot)),
    );
  }, []);

  const removeFolder = useCallback(async (folderId: string) => {
    const soundIds = await deleteFolder(folderId);
    for (const soundId of soundIds) forgetBuffer(soundId);
    setSounds((prev) => prev.filter((sound) => !soundIds.includes(sound.soundId)));
    setFolders((prev) => prev.filter((folder) => folder.folderId !== folderId));
    setMap((prev) =>
      prev.map((slot) => (slot?.mode === "folder" && slot.folderId === folderId ? null : slot)),
    );
  }, []);

  const occupied = useCallback(
    (id: CategoryId) =>
      librarySounds.some((sound) => sound.kind === id) || folders.some((folder) => folder.kind === id),
    [folders, librarySounds],
  );

  const createCategory = useCallback(async () => {
    const current = categoriesRef.current;
    const used = current.map((row) => row.color);
    const nth = current.filter((row) => /^Catégorie( \d+)?$/.test(row.title)).length;
    const order = current.reduce((max, row) => Math.max(max, row.order), -1) + 1;
    const category: LibraryCategory = {
      id: crypto.randomUUID(),
      title: nth === 0 ? "Catégorie" : `Catégorie ${nth + 1}`,
      color: nextCategoryColor(used),
      order,
    };
    await putCategory(category);
    setCategories((prev) => [...prev, category]);
  }, []);

  const renameCategory = useCallback(async (id: CategoryId, title: string) => {
    const current = categoriesRef.current.find((row) => row.id === id);
    if (!current) return;
    const nextTitle = title.trim() || current.title;
    if (nextTitle === current.title) return;
    const next = { ...current, title: nextTitle };
    try {
      await putCategory(next);
      setCategories((prev) => prev.map((row) => (row.id === id ? next : row)));
    } catch {
      show("Renommage impossible.");
    }
  }, []);

  const recolorCategory = useCallback(async (id: CategoryId, color: PadColor) => {
    const current = categoriesRef.current.find((row) => row.id === id);
    if (!current || current.color === color) return;
    const next = { ...current, color };
    try {
      await putCategory(next);
      setCategories((prev) => prev.map((row) => (row.id === id ? next : row)));
    } catch {
      show("Couleur impossible.");
    }
  }, []);

  const removeCategory = useCallback(
    async (id: CategoryId) => {
      if (occupied(id)) {
        show("Vide la catégorie d’abord.");
        return;
      }
      await deleteCategory(id);
      setCategories((prev) => prev.filter((row) => row.id !== id));
      if (lastImportKind.current === id) lastImportKind.current = null;
    },
    [occupied],
  );

  const fireAssign = useCallback(
    async (note: number, slot: PadAssign) => {
      pulse(note);
      let soundId: string;
      let title: string;
      let kind = slot.kind;
      if (slot.mode === "folder") {
        const folder = folders.find((row) => row.folderId === slot.folderId);
        const pick = pickRandomExcept(folder?.soundIds ?? [], lastPlayed.current[note] ?? null);
        if (!pick) {
          show("Dossier vide.");
          return;
        }
        lastPlayed.current[note] = pick;
        const meta = librarySounds.find((sound) => sound.soundId === pick);
        soundId = pick;
        title = meta?.title ?? slot.title;
        kind = meta?.kind ?? slot.kind;
      } else {
        soundId = slot.soundId;
        title = slot.title;
      }
      let blob = await getSoundBlob(soundId);
      if (!blob) {
        const catalogId =
          (slot.mode === "one" && slot.catalogId) ||
          librarySounds.find((sound) => sound.soundId === soundId)?.catalogId;
        if (catalogId) {
          await ensureCatalogCache(soundId, catalogId);
          blob = await getSoundBlob(soundId);
        }
      }
      if (!blob) {
        show("Son introuvable.");
        return;
      }
      try {
        await playSound(soundId, blob, note, title);
        if (kind === MUSIC_ID && chainAfterSound) setJingleArmed(true);
        if (kind === JINGLE_ID) setJingleArmed(false);
      } catch {
        show("Lecture impossible.");
      }
    },
    [chainAfterSound, folders, librarySounds],
  );

  const fireNote = useCallback(
    (note: number) => {
      const slot = map[note];
      if (!slot) return;
      if (firing.current.has(note) || isNotePlaying(note)) return;
      firing.current.add(note);
      void fireAssign(note, slot).finally(() => {
        firing.current.delete(note);
      });
    },
    [fireAssign, map],
  );

  const skip = useCallback(() => {
    stopAll();
    setNow(null);
  }, []);

  const toggleChain = useCallback(() => {
    setChainAfterSound((prev) => !prev);
  }, []);

  const remaining = Math.max(0, now?.remaining ?? 0);
  const duration = now?.duration ?? null;
  const onAirNote = now?.note ?? null;
  const endingSoon = onAirNote != null && remaining > 0 && remaining <= 5;
  const live = mode === "live";

  const armedNotes = useMemo(() => {
    const next = new Set<number>();
    if (!jingleArmed) return next;
    map.forEach((slot, note) => {
      if (slot?.kind === JINGLE_ID) next.add(note);
    });
    return next;
  }, [jingleArmed, map]);

  const { status: midi, connect } = useApcMini({
    map,
    onFire: fireNote,
    onSkip: skip,
    onToggleChain: toggleChain,
    playingNote: onAirNote,
    endingSoon,
    jingleArmed,
    chainAfterSound,
  });

  const moveToCategory = useCallback(
    async (source: DragSource, categoryId: CategoryId) => {
      const category = categories.find((row) => row.id === categoryId);
      if (!category) return;
      const color = category.color;
      if (source.type === "lib") {
        if (source.sound.kind === categoryId) return;
        const updated = await recategorizeSound(source.sound.soundId, categoryId, color);
        if (!updated) return;
        setSounds((prev) => prev.map((sound) => (sound.soundId === updated.soundId ? updated : sound)));
        setMap((prev) =>
          prev.map((slot) =>
            slot?.mode === "one" && slot.soundId === updated.soundId
              ? { ...slot, kind: categoryId, color }
              : slot,
          ),
        );
        lastImportKind.current = categoryId;
        return;
      }
      if (source.type !== "folder" || source.folder.kind === categoryId) return;
      const moved = await recategorizeFolder(source.folder.folderId, categoryId, color);
      if (!moved) return;
      setFolders((prev) =>
        prev.map((folder) => (folder.folderId === moved.folder.folderId ? moved.folder : folder)),
      );
      setSounds((prev) =>
        prev.map((sound) =>
          moved.soundIds.includes(sound.soundId) ? { ...sound, kind: categoryId, color } : sound,
        ),
      );
      setMap((prev) =>
        prev.map((slot) =>
          slot?.mode === "folder" && slot.folderId === moved.folder.folderId
            ? { ...slot, kind: categoryId, color }
            : slot,
        ),
      );
      lastImportKind.current = categoryId;
    },
    [categories],
  );

  const onDrop = useCallback(
    (source: DragSource, target: { note: number | null; categoryId: CategoryId | null }) => {
      const { note: overNote, categoryId } = target;
      if (source.type === "lib" || source.type === "folder") {
        if (overNote != null) {
          delete lastPlayed.current[overNote];
          setMap((prev) => {
            const next = [...prev];
            next[overNote] = source.type === "lib" ? toAssign(source.sound) : toFolderAssign(source.folder);
            return next;
          });
          if (source.type === "lib") {
            void ensureCatalogCache(source.sound.soundId, source.sound.catalogId);
          }
          return;
        }
        if (categoryId) void moveToCategory(source, categoryId);
        return;
      }
      if (overNote == null) {
        delete lastPlayed.current[source.note];
        setMap((prev) => {
          const next = [...prev];
          next[source.note] = null;
          return next;
        });
        return;
      }
      if (overNote === source.note) return;
      const from = lastPlayed.current[source.note];
      const to = lastPlayed.current[overNote];
      if (from) lastPlayed.current[overNote] = from;
      else delete lastPlayed.current[overNote];
      if (to) lastPlayed.current[source.note] = to;
      else delete lastPlayed.current[source.note];
      setMap((prev) => {
        const next = [...prev];
        const moving = next[source.note] ?? null;
        next[source.note] = next[overNote] ?? null;
        next[overNote] = moving;
        return next;
      });
    },
    [moveToCategory],
  );

  return (
    <SurfaceDrag onDrop={onDrop} locked={live}>
      <div className={`surface is-${mode}`}>
        <TopBar
          title={now?.title ?? "Silence"}
          remaining={now ? remaining : null}
          playing={now != null}
          midi={midi}
          chainAfterSound={chainAfterSound}
          mode={mode}
          libraryOpen={libraryOpen}
          onMode={setMode}
          onToggleLibrary={() => setLibraryOpen((open) => !open)}
          onToggleChain={toggleChain}
          onSkip={skip}
          onConnectMidi={connect}
        />
        {live && libraryOpen ? (
          <button
            type="button"
            className="rail-scrim"
            aria-label="Fermer la bibliothèque"
            onClick={() => setLibraryOpen(false)}
          />
        ) : null}
        <SoundRail
          categories={categories}
          sounds={librarySounds}
          folders={folders}
          importing={importing}
          readOnly={live}
          open={live ? libraryOpen : true}
          focusSearch={live && libraryOpen}
          onClose={live ? () => setLibraryOpen(false) : undefined}
          onImport={(files, kind, folderName) => void ingest(files, kind, { folderName })}
          onQueryChange={(query, kind) => void loadCatalog(query, kind)}
          onRemoveSound={(soundId) => void removeSound(soundId)}
          onRemoveFolder={(folderId) => void removeFolder(folderId)}
          onCreateCategory={() => void createCategory()}
          onRenameCategory={(id, title) => void renameCategory(id, title)}
          onRecolorCategory={(id, color) => void recolorCategory(id, color)}
          onRemoveCategory={(id) => void removeCategory(id)}
        />
        <main className="stage">
          <PadGrid
            map={map}
            folders={folders}
            categories={categories}
            firedNote={firedNote}
            onAirNote={onAirNote}
            remaining={now ? remaining : null}
            duration={now ? duration : null}
            endingSoon={endingSoon}
            armedNotes={armedNotes}
            locked={live}
            onFire={fireNote}
            onMedia={(note, files, folderName) => {
              if (live) return;
              const kind = lastImportKind.current ?? categories[0]?.id;
              if (!kind) {
                show("Crée une catégorie d’abord.");
                return;
              }
              void ingest(files, kind, { note, folderName });
            }}
            onColor={(note, color) => {
              setMap((prev) => {
                const slot = prev[note];
                if (!slot) return prev;
                const next = [...prev];
                next[note] = { ...slot, color };
                return next;
              });
            }}
          />
        </main>
        {toast ? <p className="toast">{toast}</p> : null}
      </div>
    </SurfaceDrag>
  );
}
