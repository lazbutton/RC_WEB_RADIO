import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { logout, type SearchHit } from "../api";
import { useAuth } from "../auth";
import { Button } from "../components/Button";
import { CommandPalette, type Command } from "../components/CommandPalette";
import { Icon } from "../components/Icon";
import { Kbd } from "../components/Kbd";
import { Sheet } from "../components/Sheet";
import { useBrand } from "../lib/BrandContext";
import { label } from "../lib/brand";
import { agoLabel } from "../lib/format";
import { useKeyboard } from "../lib/keyboard";
import { useStore } from "../lib/store";

const WEBMAIL = "https://webmail.radiocampus.org";

const SHORTCUTS: { keys: string[]; label: string }[] = [
  { keys: ["j", "k"], label: "Mail suivant / précédent" },
  { keys: ["e"], label: "Archiver dans Inbox Zero / catégorie" },
  { keys: ["u"], label: "Lu / non lu" },
  { keys: ["s"], label: "Drapeau" },
  { keys: ["l"], label: "Plus tard (sort de la file, reste dans la boîte)" },
  { keys: ["n"], label: "Créer la tâche Notion" },
  { keys: ["c"], label: "Copier l’exemple de réponse" },
  { keys: ["x"], label: "Sélectionner le mail" },
  { keys: ["⇧E"], label: "Archiver la sélection" },
  { keys: ["z"], label: "Annuler la dernière action" },
  { keys: ["r"], label: "Historique : remettre dans la boîte / à trier" },
  { keys: ["/"], label: "Chercher dans tous les mails" },
  { keys: ["⌘K"], label: "Palette de commandes" },
  { keys: ["g"], label: "Basculer À trier / Historique" },
  { keys: ["?"], label: "Cette aide" },
];

export function Shell() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { onLost } = useAuth();
  const brand = useBrand();
  const store = useStore();
  const [help, setHelp] = useState(false);
  const [palette, setPalette] = useState(false);
  const [, tick] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => tick((n) => n + 1), 30_000);
    const onHelp = () => setHelp(true);
    window.addEventListener("inboxzero-help", onHelp);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("inboxzero-help", onHelp);
    };
  }, []);

  useKeyboard({
    "?": () => setHelp((prev) => !prev),
    "shift+?": () => setHelp((prev) => !prev),
    "mod+k": () => setPalette((prev) => !prev),
  });

  async function onLogout() {
    await logout();
    onLost();
  }

  const proposed = store.queue?.counts?.proposed || 0;
  const unread = store.queue?.unread || 0;
  const lastScan = store.queue?.last_scan_at || null;
  const incident = store.incident;

  const commands = useMemo<Command[]>(() => {
    const items = store.queue?.items ?? [];
    const newsletters = items.filter((row) => row.category === "newsletters" && (store.items[row.id] ?? row).status === "proposed").map((row) => row.id);
    const unseen = items.filter((row) => (store.items[row.id] ?? row).seen === false).map((row) => row.id);
    return [
      { id: "scan", group: "Actions", label: "Relever la boîte", hint: "IMAP + tri Claude", icon: "refresh", run: () => void store.startScan() },
      { id: "undo", group: "Actions", label: "Annuler la dernière action", icon: "undo", shortcut: "z", run: () => void store.undoLast() },
      {
        id: "archive-news",
        group: "Actions",
        label: "Archiver toutes les newsletters",
        hint: newsletters.length ? `${newsletters.length} mails → Inbox Zero/Newsletters` : "aucune newsletter",
        icon: "archive",
        disabled: !newsletters.length || !store.queue?.can_move,
        run: () => void store.act("archive", newsletters, { label: "Newsletters archivées" }),
      },
      {
        id: "seen-all",
        group: "Actions",
        label: "Tout marquer lu",
        hint: unseen.length ? `${unseen.length} non lus` : "tout est lu",
        icon: "mail-open",
        disabled: !unseen.length,
        run: () => void store.act("seen", unseen, { silent: true }),
      },
      { id: "nav-queue", group: "Navigation", label: "À trier", icon: "inbox", run: () => navigate("/") },
      { id: "nav-history", group: "Navigation", label: "Historique", icon: "history", shortcut: "g", run: () => navigate("/history") },
      { id: "nav-settings", group: "Navigation", label: "Réglages", icon: "settings", run: () => navigate("/settings") },
      { id: "nav-webmail", group: "Navigation", label: "Ouvrir le webmail", icon: "external", run: () => window.open(WEBMAIL, "_blank", "noopener") },
      { id: "help", group: "Navigation", label: "Raccourcis clavier", icon: "keyboard", shortcut: "?", run: () => setHelp(true) },
    ];
  }, [navigate, store]);

  function openHit(hit: SearchHit) {
    const params = new URLSearchParams({ q: hit.subject.split(/\s+/).slice(0, 3).join(" "), hid: String(hit.id) });
    if (hit.item_id) params.set("id", String(hit.item_id));
    navigate(`/?${params}`);
  }

  return (
    <div className="shell">
      <header className="top">
        <div className="top-left">
          <span className="brand">{label(brand, "inbox", "title")}</span>
          <nav className="nav" aria-label="Vues">
            <NavLink to="/" end className={({ isActive }) => `nav-link${isActive ? " is-on" : ""}`}>
              <Icon name="inbox" />
              <span>À trier</span>
              {proposed ? <span className="nav-count">{proposed}</span> : null}
            </NavLink>
            <NavLink to="/history" className={({ isActive }) => `nav-link${isActive ? " is-on" : ""}`}>
              <Icon name="history" />
              <span>Historique</span>
            </NavLink>
          </nav>
        </div>
        <div className="top-right">
          <button type="button" className={`status-pill${store.scanRunning ? " is-busy" : ""}${incident ? " is-warn" : ""}`} onClick={() => void store.startScan()} title="Relever maintenant" disabled={store.scanRunning}>
            <span className={`status-dot${store.connected ? " is-live" : ""}`} aria-hidden />
            <span className="status-text">
              {store.scanRunning ? "Relevé en cours" : incident ? incident : `Relevé ${agoLabel(lastScan)}`}
              {!store.scanRunning && !incident && unread ? <span className="status-sub"> · {unread} non lu{unread > 1 ? "s" : ""}</span> : null}
            </span>
            <Icon name="refresh" className="status-icon" />
          </button>
          <Button variant="icon" icon="keyboard" onClick={() => setPalette(true)} aria-label="Palette de commandes (⌘K)" tip="⌘K">
            Palette
          </Button>
          <NavLink to="/settings" className={({ isActive }) => `top-link${isActive ? " is-on" : ""}`}>
            Réglages
          </NavLink>
          <a className="top-link" href={WEBMAIL} target="_blank" rel="noreferrer">
            Webmail
          </a>
          <button type="button" className="top-link" onClick={() => void onLogout()}>
            Sortir
          </button>
        </div>
      </header>
      {incident && pathname !== "/settings" ? (
        <div className="incident" role="status" aria-live="polite">
          <Icon name="dot" size={10} /> {incident}
        </div>
      ) : null}
      <Outlet />
      <Sheet open={help} title="Raccourcis clavier" onClose={() => setHelp(false)}>
        <ul className="shortcut-list">
          {SHORTCUTS.map((row) => (
            <li key={row.label}>
              <span className="shortcut-keys">
                {row.keys.map((key) => (
                  <Kbd key={key}>{key}</Kbd>
                ))}
              </span>
              <span>{row.label}</span>
            </li>
          ))}
        </ul>
      </Sheet>
      <CommandPalette open={palette} onClose={() => setPalette(false)} commands={commands} onOpenHit={openHit} />
    </div>
  );
}
