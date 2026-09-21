import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { logout } from "../api";
import { markRead, notifications as fetchNotifications, type Notification, type SearchHitRow } from "../api/client";
import { useAuth } from "../auth";
import { Button } from "../components/Button";
import { CommandPalette, type Command } from "../components/CommandPalette";
import { Icon, type IconName } from "../components/Icon";
import { Kbd } from "../components/Kbd";
import { Sheet } from "../components/Sheet";
import { agoLabel } from "../lib/format";
import { useKeyboard } from "../lib/keyboard";
import { useRegistry } from "../lib/registry";
import { useStore } from "../lib/store";

const WEBMAIL = "https://webmail.radiocampus.org";

type NavItem = { to: string; label: string; icon: IconName; module: string; end?: boolean; count?: number };

const SHORTCUTS: { keys: string[]; label: string }[] = [
  { keys: ["⌘K"], label: "Palette : commandes et recherche dans tout Régie" },
  { keys: ["g", "puis", "t"], label: "Aujourd’hui" },
  { keys: ["g", "puis", "m"], label: "Mails" },
  { keys: ["g", "puis", "c"], label: "Contacts" },
  { keys: ["g", "puis", "p"], label: "Planning" },
  { keys: ["j", "k"], label: "Mails : suivant / précédent" },
  { keys: ["e"], label: "Mails : archiver" },
  { keys: ["u"], label: "Mails : lu / non lu" },
  { keys: ["s"], label: "Mails : drapeau" },
  { keys: ["l"], label: "Mails : plus tard" },
  { keys: ["z"], label: "Annuler la dernière action" },
  { keys: ["/"], label: "Chercher dans tous les mails" },
  { keys: ["?"], label: "Cette aide" },
];

export function Shell() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { onLost } = useAuth();
  const reg = useRegistry();
  const store = useStore();
  const [help, setHelp] = useState(false);
  const [palette, setPalette] = useState(false);
  const [bell, setBell] = useState(false);
  const [notes, setNotes] = useState<Notification[]>([]);
  const [unreadNotes, setUnreadNotes] = useState(0);
  const [pendingG, setPendingG] = useState(false);
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

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetchNotifications(false)
        .then((data) => {
          if (!alive) return;
          setNotes(data.notifications);
          setUnreadNotes(data.unread);
        })
        .catch(() => undefined);
    void load();
    const onEvent = (event: Event) => {
      const detail = (event as CustomEvent<{ type: string }>).detail;
      if (detail?.type === "notification") void load();
    };
    window.addEventListener("regie-event", onEvent);
    return () => {
      alive = false;
      window.removeEventListener("regie-event", onEvent);
    };
  }, []);

  useKeyboard({
    "?": () => setHelp((prev) => !prev),
    "shift+?": () => setHelp((prev) => !prev),
    "mod+k": () => setPalette((prev) => !prev),
    g: () => {
      setPendingG(true);
      window.setTimeout(() => setPendingG(false), 1200);
    },
    t: () => { if (pendingG) navigate("/"); },
    m: () => { if (pendingG) navigate("/mails"); },
    c: () => { if (pendingG) navigate("/contacts"); },
    p: () => { if (pendingG) navigate("/planning"); },
  });

  async function onLogout() {
    await logout();
    onLost();
  }

  const proposed = store.queue?.counts?.proposed || 0;
  const incident = store.incident;

  const nav = useMemo<NavItem[]>(() => {
    const items: NavItem[] = [
      { to: "/", label: "Aujourd’hui", icon: "home", module: "planning", end: true },
      { to: "/mails", label: "Mails", icon: "inbox", module: "mail", count: proposed },
      { to: "/contacts", label: "Contacts", icon: "users", module: "contacts" },
      { to: "/events", label: "Événements", icon: "calendar", module: "events" },
      { to: "/planning", label: "Planning", icon: "check", module: "planning" },
      { to: "/shows", label: "Émissions", icon: "radio", module: "shows" },
      { to: "/publish", label: "Publier", icon: "send", module: "publish" },
      { to: "/radio", label: "Vie de la radio", icon: "mic", module: "radio" },
      { to: "/files", label: "Fichiers", icon: "file", module: "files" },
    ];
    return items.filter((item) => reg.can(item.module));
  }, [reg, proposed]);

  const commands = useMemo<Command[]>(() => {
    const items = store.queue?.items ?? [];
    const newsletters = items.filter((row) => row.category === "newsletters" && (store.items[row.id] ?? row).status === "proposed").map((row) => row.id);
    const out: Command[] = [
      { id: "scan", group: "Actions", label: "Relever la boîte mail", hint: "IMAP + tri", icon: "refresh", run: () => void store.startScan() },
      { id: "undo", group: "Actions", label: "Annuler la dernière action", icon: "undo", shortcut: "z", run: () => void store.undoLast() },
      { id: "archive-news", group: "Actions", label: "Archiver toutes les newsletters", hint: newsletters.length ? `${newsletters.length} mails` : "aucune", icon: "archive", disabled: !newsletters.length || !store.queue?.can_move, run: () => void store.act("archive", newsletters, { label: "Newsletters archivées" }) },
      { id: "new-task", group: "Actions", label: "Nouvelle tâche", icon: "plus", run: () => navigate("/planning?new=1") },
      { id: "new-person", group: "Actions", label: "Nouvelle fiche contact", icon: "user", run: () => navigate("/contacts?new=person") },
    ];
    for (const item of nav) out.push({ id: `nav-${item.to}`, group: "Navigation", label: item.label, icon: item.icon, run: () => navigate(item.to) });
    out.push({ id: "nav-settings", group: "Navigation", label: "Réglages", icon: "settings", run: () => navigate("/settings") });
    out.push({ id: "nav-system", group: "Navigation", label: "État du système", icon: "activity", run: () => navigate("/system") });
    out.push({ id: "nav-webmail", group: "Navigation", label: "Ouvrir le webmail", icon: "external", run: () => window.open(WEBMAIL, "_blank", "noopener") });
    out.push({ id: "help", group: "Navigation", label: "Raccourcis clavier", icon: "keyboard", shortcut: "?", run: () => setHelp(true) });
    return out;
  }, [navigate, store, nav]);

  function openHit(hit: SearchHitRow) {
    if (hit.kind === "mail_index") {
      const params = new URLSearchParams({ q: (hit.title || "").split(/\s+/).slice(0, 3).join(" "), hid: String(hit.id) });
      const itemId = (hit.entity as { item_id?: number } | null | undefined)?.item_id;
      if (itemId) params.set("id", String(itemId));
      navigate(`/mails?${params}`);
      return;
    }
    navigate(hit.url || reg.routeFor(hit.kind, hit.id));
  }

  async function openNotification(note: Notification) {
    if (!note.read_at) {
      await markRead([note.id]).catch(() => undefined);
      setNotes((prev) => prev.map((n) => (n.id === note.id ? { ...n, read_at: new Date().toISOString() } : n)));
      setUnreadNotes((n) => Math.max(0, n - 1));
    }
    setBell(false);
    if (note.url) navigate(note.url);
  }

  return (
    <div className="shell has-side">
      <aside className="side" aria-label="Modules">
        <NavLink to="/" end className="brand side-brand">
          Régie
        </NavLink>
        <nav className="side-nav">
          {nav.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => `side-link${isActive ? " is-on" : ""}`}>
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {item.count ? <span className="nav-count">{item.count}</span> : null}
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          <NavLink to="/system" className={({ isActive }) => `side-link is-small${isActive ? " is-on" : ""}`}>
            <Icon name="activity" />
            <span>État du système</span>
          </NavLink>
          <NavLink to="/settings" className={({ isActive }) => `side-link is-small${isActive ? " is-on" : ""}`}>
            <Icon name="settings" />
            <span>Réglages</span>
          </NavLink>
        </div>
      </aside>
      <div className="main">
        <header className="top">
          <div className="top-left">
            <button type="button" className="search-trigger" onClick={() => setPalette(true)}>
              <Icon name="search" />
              <span>Chercher partout…</span>
              <Kbd>⌘K</Kbd>
            </button>
          </div>
          <div className="top-right">
            {pathname.startsWith("/mails") ? (
              <button type="button" className={`status-pill${store.scanRunning ? " is-busy" : ""}${incident ? " is-warn" : ""}`} onClick={() => void store.startScan()} title="Relever maintenant" disabled={store.scanRunning}>
                <span className={`status-dot${store.connected ? " is-live" : ""}`} aria-hidden />
                <span className="status-text">{store.scanRunning ? "Relevé en cours" : incident ? incident : `Relevé ${agoLabel(store.queue?.last_scan_at || null)}`}</span>
                <Icon name="refresh" className="status-icon" />
              </button>
            ) : (
              <span className={`live-dot${store.connected ? " is-live" : ""}`} title={store.connected ? "Temps réel connecté" : "Temps réel hors ligne"} aria-hidden />
            )}
            <Button variant="icon" icon="bell" onClick={() => setBell(true)} aria-label="Notifications" tip="Notifications">
              {unreadNotes ? <span className="bell-count">{unreadNotes}</span> : null}
            </Button>
            <span className="top-user" title={reg.me?.user.email}>
              {reg.me?.user.name || reg.me?.user.email}
            </span>
            <button type="button" className="top-link" onClick={() => void onLogout()}>
              Sortir
            </button>
          </div>
        </header>
        {incident && pathname.startsWith("/mails") ? (
          <div className="incident" role="status" aria-live="polite">
            <Icon name="dot" size={10} /> {incident}
          </div>
        ) : null}
        <Outlet />
      </div>
      <Sheet open={help} title="Raccourcis clavier" onClose={() => setHelp(false)}>
        <ul className="shortcut-list">
          {SHORTCUTS.map((row) => (
            <li key={row.label}>
              <span className="shortcut-keys">
                {row.keys.map((key) => (key === "puis" ? <span key={key} className="shortcut-sep">puis</span> : <Kbd key={key}>{key}</Kbd>))}
              </span>
              <span>{row.label}</span>
            </li>
          ))}
        </ul>
      </Sheet>
      <Sheet open={bell} title="Notifications" onClose={() => setBell(false)}>
        {notes.length ? (
          <ul className="note-list">
            {notes.map((note) => (
              <li key={note.id} className={note.read_at ? "" : "is-unread"}>
                <button type="button" onClick={() => void openNotification(note)}>
                  <span className="note-text">{note.text}</span>
                  <span className="note-when">{agoLabel(note.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">Rien pour l’instant.</p>
        )}
        {unreadNotes ? (
          <Button variant="ghost" onClick={() => void markRead().then(() => { setUnreadNotes(0); setNotes((prev) => prev.map((n) => ({ ...n, read_at: n.read_at || new Date().toISOString() }))); })}>
            Tout marquer lu
          </Button>
        ) : null}
      </Sheet>
      <CommandPalette open={palette} onClose={() => setPalette(false)} commands={commands} onOpenHit={openHit} />
    </div>
  );
}
