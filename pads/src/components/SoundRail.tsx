import { useEffect, useMemo, useRef, useState, type DragEvent, type MouseEvent } from "react";
import { FolderPlus, Plus, Shuffle, Trash2, X } from "lucide-react";
import { ColorMenu } from "./ColorMenu";
import { formatMmSs } from "../lib/format";
import { collectDroppedFiles, folderNameFromFiles } from "../lib/dropFiles";
import { PAD_COLORS } from "../lib/padColor";
import { useSurfaceDrag } from "../drag/surfaceDrag";
import type { CategoryId, LibraryCategory, LibraryFolder, LibrarySound, PadColor } from "../types";

export function SoundRail({
  categories,
  sounds,
  folders,
  importing,
  readOnly,
  open,
  focusSearch,
  onClose,
  onImport,
  onQueryChange,
  onRemoveSound,
  onRemoveFolder,
  onCreateCategory,
  onRenameCategory,
  onRecolorCategory,
  onRemoveCategory,
}: {
  categories: LibraryCategory[];
  sounds: LibrarySound[];
  folders: LibraryFolder[];
  importing: boolean;
  readOnly?: boolean;
  open?: boolean;
  focusSearch?: boolean;
  onClose?: () => void;
  onImport: (files: File[], kind: CategoryId, folderName?: string | null) => void;
  onQueryChange?: (query: string, kind: CategoryId | null) => void;
  onRemoveSound: (soundId: string) => void;
  onRemoveFolder: (folderId: string) => void;
  onCreateCategory: () => void;
  onRenameCategory: (id: CategoryId, title: string) => void;
  onRecolorCategory: (id: CategoryId, color: PadColor) => void;
  onRemoveCategory: (id: CategoryId) => void;
}) {
  const nested = useMemo(() => new Set(folders.flatMap((folder) => folder.soundIds)), [folders]);
  const [colorMenu, setColorMenu] = useState<{ id: CategoryId; x: number; y: number } | null>(null);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<CategoryId | null>(categories[0]?.id ?? null);
  const searchRef = useRef<HTMLInputElement>(null);
  const menuCategory = colorMenu ? categories.find((row) => row.id === colorMenu.id) : null;
  const active = categories.find((row) => row.id === tab) ?? categories[0] ?? null;
  const needle = query.trim().toLowerCase();
  const matchTitle = (title: string) => !needle || title.toLowerCase().includes(needle);
  const matchSound = (sound: LibrarySound) =>
    sound.source === "button" || sound.soundId.startsWith("button:") || matchTitle(sound.title);
  const activeFolders = active
    ? folders.filter((folder) => folder.kind === active.id && matchTitle(folder.title))
    : [];
  const activeItems = active
    ? sounds.filter((sound) => sound.kind === active.id && !nested.has(sound.soundId) && matchSound(sound))
    : [];

  useEffect(() => {
    onQueryChange?.(query, tab);
  }, [onQueryChange, query, tab]);

  useEffect(() => {
    if (!categories.some((row) => row.id === tab)) setTab(categories[0]?.id ?? null);
  }, [categories, tab]);

  useEffect(() => {
    if (focusSearch) searchRef.current?.focus();
  }, [focusSearch]);

  return (
    <aside
      className={`rail${open ? " is-open" : ""}${readOnly ? " is-readonly" : ""}`}
      aria-label="Bibliothèque"
      aria-hidden={Boolean(readOnly && !open)}
      inert={readOnly && !open}
    >
      <header className="rail-head">
        <div className="rail-head-row">
          <div>
            <p className="rail-kicker">Bibliothèque</p>
            <h2>Sons</h2>
          </div>
          {onClose ? (
            <button type="button" className="rail-add" onClick={onClose} aria-label="Fermer la bibliothèque">
              <X size={14} aria-hidden="true" />
            </button>
          ) : null}
        </div>
        <p className="rail-hint">
          {readOnly ? "Consultation — passer en Régler pour mapper" : "Glisser un son vers une catégorie ou la grille"}
        </p>
        <input
          ref={searchRef}
          className="rail-search"
          type="search"
          placeholder="Rechercher…"
          value={query}
          aria-label="Rechercher un son"
          onChange={(event) => setQuery(event.target.value)}
        />
        {categories.length ? (
          <div className="rail-tabs" role="tablist" aria-label="Catégories">
            {categories.map((category) => {
              const hex = PAD_COLORS.find((row) => row.id === category.color)?.hex ?? "#e0ae58";
              const on = active?.id === category.id;
              return (
                <button
                  key={category.id}
                  type="button"
                  role="tab"
                  aria-selected={on}
                  className={`rail-tab${on ? " is-on" : ""}`}
                  onClick={() => setTab(category.id)}
                >
                  <span className="rail-tab-dot" style={{ background: hex }} />
                  {category.title}
                </button>
              );
            })}
          </div>
        ) : null}
      </header>
      <div className="rail-body">
        {active ? (
          <KindSection
            key={active.id}
            category={active}
            importing={importing}
            readOnly={Boolean(readOnly)}
            folders={activeFolders}
            items={activeItems}
            emptyHint={
              needle
                ? "Aucun résultat"
                : readOnly
                  ? "Rien dans cette catégorie"
                  : "Banque Nasgul ou fichiers locaux"
            }
            onImport={onImport}
            onRemoveSound={onRemoveSound}
            onRemoveFolder={onRemoveFolder}
            onRename={(title) => onRenameCategory(active.id, title)}
            onColorMenu={(event) => {
              if (readOnly) return;
              event.preventDefault();
              event.stopPropagation();
              setColorMenu({ id: active.id, x: event.clientX, y: event.clientY });
            }}
            onRemove={() => onRemoveCategory(active.id)}
          />
        ) : (
          <p className="rail-empty">Crée une catégorie d’abord</p>
        )}
        {readOnly ? null : (
          <button type="button" className="rail-new" onClick={onCreateCategory}>
            Nouvelle catégorie
          </button>
        )}
      </div>
      {colorMenu && menuCategory && !readOnly ? (
        <ColorMenu
          x={colorMenu.x}
          y={colorMenu.y}
          current={menuCategory.color}
          onPick={(color) => {
            onRecolorCategory(menuCategory.id, color);
            setColorMenu(null);
          }}
          onClose={() => setColorMenu(null)}
        />
      ) : null}
    </aside>
  );
}

function KindSection({
  category,
  items,
  folders,
  importing,
  readOnly,
  emptyHint,
  onImport,
  onRemoveSound,
  onRemoveFolder,
  onRename,
  onColorMenu,
  onRemove,
}: {
  category: LibraryCategory;
  items: LibrarySound[];
  folders: LibraryFolder[];
  importing: boolean;
  readOnly: boolean;
  emptyHint: string;
  onImport: (files: File[], kind: CategoryId, folderName?: string | null) => void;
  onRemoveSound: (soundId: string) => void;
  onRemoveFolder: (folderId: string) => void;
  onRename: (title: string) => void;
  onColorMenu: (event: MouseEvent<HTMLElement>) => void;
  onRemove: () => void;
}) {
  const { session } = useSurfaceDrag();
  const over =
    !readOnly &&
    (session?.source.type === "lib" || session?.source.type === "folder") &&
    session.overCategory === category.id;
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState(category.title);
  const titleRef = useRef(title);
  titleRef.current = title;
  const occupied = items.length > 0 || folders.length > 0;
  const swatch = PAD_COLORS.find((row) => row.id === category.color)?.hex ?? "#e0ae58";

  useEffect(() => {
    setTitle(category.title);
  }, [category.title]);

  useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", "");
    folderInput.current?.setAttribute("directory", "");
  }, []);

  const takeFiles = (list: FileList | File[] | null, folderName?: string | null) => {
    if (readOnly || !list || list.length === 0) return;
    const files = Array.from(list);
    onImport(files, category.id, folderName ?? folderNameFromFiles(files));
  };

  const onFileDrag = (event: DragEvent) => {
    if (readOnly || ![...event.dataTransfer.types].includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  };

  return (
    <section
      className={`rail-cart is-drop${importing ? " is-busy" : ""}${over ? " is-over" : ""}`}
      data-category-id={category.id}
      onDragEnter={onFileDrag}
      onDragOver={onFileDrag}
      onDrop={(event) => {
        if (readOnly || ![...event.dataTransfer.types].includes("Files")) return;
        event.preventDefault();
        void collectDroppedFiles(event).then(({ files, folderName }) => takeFiles(files, folderName));
      }}
    >
      <div className="rail-cart-head">
        <button
          type="button"
          className="rail-swatch"
          style={{ background: swatch }}
          aria-label={`Couleur de ${category.title}`}
          disabled={readOnly}
          onClick={onColorMenu}
        />
        {readOnly ? (
          <p className="rail-cat-title is-static">{category.title}</p>
        ) : (
          <input
            className="rail-cat-title"
            value={title}
            aria-label="Nom de la catégorie"
            onChange={(event) => {
              const value = event.target.value;
              setTitle(value);
              onRename(value);
            }}
            onBlur={() => onRename(titleRef.current)}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
          />
        )}
        {readOnly ? null : (
          <div className="rail-adds">
            <button
              type="button"
              className="rail-add"
              disabled={importing}
              onClick={() => fileInput.current?.click()}
              aria-label={`Importer des fichiers dans ${category.title}`}
              title="Fichiers"
            >
              <Plus size={14} aria-hidden="true" />
            </button>
            <button
              type="button"
              className="rail-add"
              disabled={importing}
              onClick={() => folderInput.current?.click()}
              aria-label={`Importer un dossier dans ${category.title}`}
              title="Dossier aléatoire"
            >
              <FolderPlus size={14} aria-hidden="true" />
            </button>
            <button
              type="button"
              className={`rail-add${occupied ? " is-off" : ""}`}
              aria-disabled={occupied}
              aria-label={`Supprimer ${category.title}`}
              title={occupied ? "Vide la catégorie d’abord" : "Supprimer"}
              onClick={onRemove}
            >
              <Trash2 size={13} aria-hidden="true" />
            </button>
          </div>
        )}
      </div>
      {readOnly ? null : (
        <>
          <input
            ref={fileInput}
            type="file"
            accept="audio/*,.mp3,.wav,.flac,.ogg,.m4a,.aiff"
            multiple
            hidden
            onChange={(event) => {
              takeFiles(event.target.files);
              event.target.value = "";
            }}
          />
          <input
            ref={folderInput}
            type="file"
            multiple
            hidden
            onChange={(event) => {
              takeFiles(event.target.files);
              event.target.value = "";
            }}
          />
        </>
      )}
      {folders.length || items.length ? (
        <ul>
          {folders.map((folder) => (
            <li key={folder.folderId}>
              <FolderItem folder={folder} readOnly={readOnly} onRemove={onRemoveFolder} />
            </li>
          ))}
          {items.map((sound) => (
            <li key={sound.soundId}>
              <RailItem sound={sound} readOnly={readOnly} onRemove={onRemoveSound} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="rail-empty">{emptyHint}</p>
      )}
    </section>
  );
}

function FolderItem({
  folder,
  readOnly,
  onRemove,
}: {
  folder: LibraryFolder;
  readOnly: boolean;
  onRemove: (folderId: string) => void;
}) {
  const { begin, session } = useSurfaceDrag();
  const dragging = session?.source.type === "folder" && session.source.folder.folderId === folder.folderId;
  return (
    <div
      className={`rail-item is-folder${dragging ? " is-dragging" : ""}`}
      data-color={folder.color}
    >
      <button
        type="button"
        className="rail-item-main"
        onPointerDown={(event) => {
          if (!readOnly) begin({ type: "folder", folder }, event);
        }}
      >
        <span className="rail-item-title">
          <Shuffle size={11} aria-hidden="true" />
          {folder.title}
        </span>
        <span className="rail-item-dur">{folder.soundIds.length}</span>
      </button>
      {readOnly ? null : (
        <button
          type="button"
          className="rail-item-del"
          aria-label={`Retirer ${folder.title}`}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={() => onRemove(folder.folderId)}
        >
          <X size={12} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

function RailItem({
  sound,
  readOnly,
  onRemove,
}: {
  sound: LibrarySound;
  readOnly: boolean;
  onRemove: (soundId: string) => void;
}) {
  const { begin, session } = useSurfaceDrag();
  const dragging = session?.source.type === "lib" && session.source.sound.soundId === sound.soundId;
  return (
    <div className={`rail-item${dragging ? " is-dragging" : ""}`} data-color={sound.color}>
      <button
        type="button"
        className="rail-item-main"
        onPointerDown={(event) => {
          if (!readOnly) begin({ type: "lib", sound }, event);
        }}
      >
        <span className="rail-item-title">{sound.title}</span>
        <span className="rail-item-dur">{formatMmSs(sound.durationSec)}</span>
      </button>
      {readOnly || sound.source === "button" || sound.soundId.startsWith("button:") ? null : (
        <button
          type="button"
          className="rail-item-del"
          aria-label={`Retirer ${sound.title}`}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={() => onRemove(sound.soundId)}
        >
          <X size={12} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
