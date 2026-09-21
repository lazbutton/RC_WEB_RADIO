import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { createUser, kernelSettings, patchMe, patchUser, permissions as fetchPermissions, saveKernelSettings, setPermission, users as fetchUsers } from "../api/client";
import { Button } from "../components/Button";
import { Card, Empty, Field, Page, Pill, Tabs, fmtDate } from "../components/Page";
import { useToasts } from "../components/Toast";
import { useRegistry } from "../lib/registry";
import { MailSettingsPage } from "./MailSettings";

type Tab = "me" | "team" | "mail" | "connectors";
const MODULES = ["mail", "contacts", "events", "planning", "calendar", "shows", "publish", "radio", "files", "settings"];
const LEVELS = ["none", "read", "write", "admin"];
const CONNECTOR_KEYS: { key: string; label: string; hint: string }[] = [
  { key: "radio.icecast_url", label: "Icecast (statistiques d’écoute)", hint: "http://icecast:8000 — relevé toutes les 5 min" },
  { key: "publish.public_url", label: "URL publique du bord (blocs iframe)", hint: "https://blocs.radiocampus.org" },
  { key: "publish.audio_base_url", label: "URL publique des podcasts (mp3)", hint: "dossier servi par le site ou un stockage" },
  { key: "publish.agenda_all", label: "Agenda public : tous les événements Outlive (1) ou seulement couverts / Radio Campus (vide)", hint: "" },
  { key: "publish.playlist_json", label: "Playlist de la semaine (JSON)", hint: '[{"week":"S38","artist":"…","title":"…"}]' },
  { key: "shows.transcribe_engine", label: "Moteur de transcription", hint: "mlx (Mac) · faster (NAS) · vide = automatique" },
  { key: "shows.whisper_model", label: "Modèle Whisper (Mac)", hint: "mlx-community/whisper-large-v3-turbo" },
  { key: "backup_dir", label: "Dossier des sauvegardes chiffrées", hint: "/backups" },
];

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const reg = useRegistry();
  const tab = (params.get("tab") as Tab) || "me";
  const items = [{ id: "me" as Tab, label: "Mon compte" }, ...(reg.can("mail") ? [{ id: "mail" as Tab, label: "Mails" }] : []), ...(reg.me?.user.role === "admin" ? [{ id: "team" as Tab, label: "Équipe et droits" }, { id: "connectors" as Tab, label: "Connecteurs" }] : [])];
  return (
    <Page title="Réglages" wide>
      <Tabs value={tab} onChange={(value) => setParams({ tab: value })} items={items} />
      {tab === "me" ? <MyAccount /> : null}
      {tab === "mail" ? <MailSettingsPage /> : null}
      {tab === "team" ? <Team /> : null}
      {tab === "connectors" ? <Connectors /> : null}
    </Page>
  );
}

function MyAccount() {
  const reg = useRegistry();
  const toasts = useToasts();
  const [name, setName] = useState(reg.me?.user.name || "");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  useEffect(() => setName(reg.me?.user.name || ""), [reg.me]);
  async function save() {
    try {
      await patchMe({ name, ...(next ? { password: next, current_password: current } : {}) });
      await reg.refresh();
      setCurrent("");
      setNext("");
      toasts.push({ text: "Compte mis à jour", tone: "ok" });
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }
  return (
    <div className="grid cols-2">
      <Card title="Identité" icon="user">
        <Field label="Adresse"><input className="field" value={reg.me?.user.email || ""} disabled /></Field>
        <Field label="Nom affiché"><input className="field" value={name} onChange={(ev) => setName(ev.target.value)} /></Field>
        <p className="small muted">Rôle : {reg.me?.user.role}</p>
      </Card>
      <Card title="Mot de passe" icon="lock">
        <Field label="Actuel"><input className="field" type="password" value={current} onChange={(ev) => setCurrent(ev.target.value)} autoComplete="current-password" /></Field>
        <Field label="Nouveau" hint="10 caractères minimum"><input className="field" type="password" value={next} onChange={(ev) => setNext(ev.target.value)} autoComplete="new-password" /></Field>
        <div className="form-foot"><Button variant="primary" onClick={() => void save()}>Enregistrer</Button></div>
      </Card>
      <Card title="Notifications" icon="bell">
        <p className="small muted">Les notifications arrivent en direct dans Régie (cloche). Pour les recevoir aussi quand l’onglet est fermé, active le Web Push (nécessite VAPID_PUBLIC_KEY / PRIVATE_KEY côté serveur).</p>
        <PushToggle />
      </Card>
    </div>
  );
}

function PushToggle() {
  const toasts = useToasts();
  const [state, setState] = useState<"unsupported" | "off" | "on" | "nokey">("off");
  useEffect(() => {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return setState("unsupported");
    fetch("/api/v1/notifications/vapid", { credentials: "include" }).then((r) => r.json()).then((data: { public_key?: string }) => setState(data.public_key ? "off" : "nokey")).catch(() => setState("nokey"));
  }, []);
  async function enable() {
    try {
      const { public_key } = (await fetch("/api/v1/notifications/vapid", { credentials: "include" }).then((r) => r.json())) as { public_key: string };
      const reg = await navigator.serviceWorker.register("/sw.js");
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(public_key) });
      await fetch("/api/v1/notifications/push", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", "X-Regie": "1" }, body: JSON.stringify({ subscription: sub.toJSON() }) });
      setState("on");
      toasts.push({ text: "Web Push activé sur cet appareil", tone: "ok" });
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "impossible", tone: "warn" });
    }
  }
  if (state === "unsupported") return <p className="small muted">Ce navigateur ne gère pas le Web Push.</p>;
  if (state === "nokey") return <p className="small muted">Clés VAPID absentes côté serveur.</p>;
  return <Button icon="bell" variant={state === "on" ? "ghost" : "primary"} onClick={() => void enable()} disabled={state === "on"}>{state === "on" ? "Activé sur cet appareil" : "Activer le Web Push ici"}</Button>;
}

function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

function Team() {
  const toasts = useToasts();
  const [rows, setRows] = useState<Awaited<ReturnType<typeof fetchUsers>>["users"]>([]);
  const [matrix, setMatrix] = useState<Record<string, Record<string, string>>>({});
  const [form, setForm] = useState({ email: "", name: "", password: "", role: "membre" });
  const load = useCallback(() => {
    fetchUsers().then((res) => setRows(res.users)).catch(() => undefined);
    fetchPermissions().then((res) => setMatrix(res.matrix)).catch(() => undefined);
  }, []);
  useEffect(load, [load]);
  async function add() {
    try {
      await createUser(form);
      setForm({ email: "", name: "", password: "", role: "membre" });
      load();
      toasts.push({ text: "Compte créé", tone: "ok" });
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }
  return (
    <div className="grid">
      <div className="grid cols-2">
        <Card title="Comptes" icon="users">
          {rows.length ? <ul className="rows">{rows.map((u) => <li key={u.id}><strong>{u.name}</strong><span className="small muted">{u.email}</span><select className="field" style={{ maxWidth: 120 }} value={u.role} onChange={(ev) => void patchUser(u.id, { role: ev.target.value }).then(load)}>{["admin", "membre", "invite"].map((r) => <option key={r}>{r}</option>)}</select>{u.disabled ? <Pill tone="warn">désactivé</Pill> : null}<span className="spacer" /><span className="small muted">{u.last_login_at ? `vu ${fmtDate(u.last_login_at)}` : "jamais connecté"}</span><Button variant="quiet" onClick={() => void patchUser(u.id, { disabled: !u.disabled }).then(load).catch((err) => toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" }))}>{u.disabled ? "Réactiver" : "Désactiver"}</Button></li>)}</ul> : <Empty text="Aucun compte." />}
        </Card>
        <Card title="Nouveau compte" icon="plus">
          <Field label="Adresse"><input className="field" type="email" value={form.email} onChange={(ev) => setForm((p) => ({ ...p, email: ev.target.value }))} /></Field>
          <Field label="Nom"><input className="field" value={form.name} onChange={(ev) => setForm((p) => ({ ...p, name: ev.target.value }))} /></Field>
          <Field label="Mot de passe provisoire" hint="10 caractères minimum ; la personne le changera dans Mon compte"><input className="field" type="text" value={form.password} onChange={(ev) => setForm((p) => ({ ...p, password: ev.target.value }))} /></Field>
          <Field label="Rôle"><select className="field" value={form.role} onChange={(ev) => setForm((p) => ({ ...p, role: ev.target.value }))}>{["admin", "membre", "invite"].map((r) => <option key={r}>{r}</option>)}</select></Field>
          <div className="form-foot"><Button variant="primary" disabled={!form.email || form.password.length < 10} onClick={() => void add()}>Créer</Button></div>
        </Card>
      </div>
      <Card title="Droits par module et par rôle" icon="shield">
        <table className="table">
          <thead><tr><th>Module</th>{["admin", "membre", "invite"].map((r) => <th key={r}>{r}</th>)}</tr></thead>
          <tbody>
            {MODULES.map((module) => (
              <tr key={module}>
                <td>{module}</td>
                {["admin", "membre", "invite"].map((role) => (
                  <td key={role}>
                    {role === "admin" ? <Pill tone="ok">admin</Pill> : <select className="field" value={matrix[role]?.[module] || "none"} onChange={(ev) => void setPermission({ role, module, level: ev.target.value }).then(load)}>{LEVELS.map((l) => <option key={l}>{l}</option>)}</select>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function Connectors() {
  const toasts = useToasts();
  const [values, setValues] = useState<Record<string, string>>({});
  const [modules, setModules] = useState<Record<string, Record<string, unknown>>>({});
  useEffect(() => {
    kernelSettings().then((res) => { setValues(res.settings); setModules(res.modules); }).catch(() => undefined);
  }, []);
  async function save() {
    try {
      await saveKernelSettings(Object.fromEntries(CONNECTOR_KEYS.map((k) => [k.key, values[k.key] || ""])));
      toasts.push({ text: "Réglages enregistrés", tone: "ok" });
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }
  return (
    <div className="grid">
      <Card title="Secrets d’environnement (secrets.env sur Nasgul)" icon="lock">
        <p className="small muted">IMAP, Claude, Notion, Outlive, Google OAuth, WordPress, bord public et VAPID se configurent dans <code>/mnt/data/apps/regie/secrets.env</code> puis <code>./regie/deploy.sh</code>. État actuel :</p>
        <div className="chips">
          {Object.entries(modules).flatMap(([name, view]) => Object.entries(view).map(([key, value]) => <Pill key={`${name}-${key}`} tone={value ? "ok" : "muted"}>{name}.{key.replace("_ready", "")} {value ? "prêt" : "absent"}</Pill>))}
        </div>
      </Card>
      <Card title="Réglages modifiables ici" icon="settings">
        {CONNECTOR_KEYS.map((item) => (
          <Field key={item.key} label={item.label} hint={item.hint}>
            {item.key.endsWith("_json") ? <textarea className="field" value={values[item.key] || ""} onChange={(ev) => setValues((p) => ({ ...p, [item.key]: ev.target.value }))} /> : <input className="field" value={values[item.key] || ""} onChange={(ev) => setValues((p) => ({ ...p, [item.key]: ev.target.value }))} />}
          </Field>
        ))}
        <div className="form-foot"><Button variant="primary" onClick={() => void save()}>Enregistrer</Button></div>
      </Card>
    </div>
  );
}
