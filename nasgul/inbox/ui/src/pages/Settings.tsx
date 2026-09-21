import { FormEvent, useEffect, useState } from "react";
import { AuthError, getSettings, saveSettings, type SettingsPayload } from "../api";
import { useAuth } from "../auth";
import { Button } from "../components/Button";
import { Chip } from "../components/Chip";
import { Icon } from "../components/Icon";
import { useToasts } from "../components/Toast";
import { agoLabel } from "../lib/format";
import { useStore } from "../lib/store";

const NOTION_INTEGRATIONS = "https://www.notion.so/profile/integrations";
const NOTION_TACHES = "https://app.notion.com/p/71b5299cc0494581954031510d690601";
const NOTION_PROJETS = "https://app.notion.com/p/962a4aa4b60e474c8d9d73bd1a68e78d";
const WEBMAIL = "https://webmail.radiocampus.org";

function modelLabel(id?: string): string {
  if (!id) return "—";
  const pretty = id.replace(/^claude-/, "").replace(/(\d)-(\d)/g, "$1.$2").replace(/-/g, " ");
  return pretty.replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

function effortLabel(effort?: string): string {
  const labels: Record<string, string> = { high: "élevé", medium: "moyen", low: "bas", xhigh: "très élevé", max: "max" };
  return labels[(effort || "").toLowerCase()] || "";
}

function StatusRow({ label, value, tone, detail }: { label: string; value: string; tone: "ok" | "warn" | "off"; detail?: string }) {
  return (
    <div className="settings-status">
      <span className={`settings-dot is-${tone}`} aria-hidden />
      <span className="settings-status-label">{label}</span>
      <span className="settings-status-value">{value}</span>
      {detail ? <span className="settings-status-detail">{detail}</span> : null}
    </div>
  );
}

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (value: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="settings-switch">
      <input type="checkbox" checked={checked} onChange={(ev) => onChange(ev.target.checked)} />
      <span className="settings-switch-copy">
        <span>{label}</span>
        {hint ? <span className="settings-meta">{hint}</span> : null}
      </span>
    </label>
  );
}

export function SettingsPage() {
  const { onLost } = useAuth();
  const toasts = useToasts();
  const store = useStore();
  const [data, setData] = useState<SettingsPayload | null>(null);
  const [prompt, setPrompt] = useState("");
  const [signature, setSignature] = useState("");
  const [notionToken, setNotionToken] = useState("");
  const [markRead, setMarkRead] = useState(false);
  const [density, setDensity] = useState("comfortable");
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState(false);

  async function load(probe = false) {
    try {
      const payload = await getSettings(probe);
      setData(payload);
      setPrompt(payload.extra_prompt);
      setSignature(payload.signature || "");
      setMarkRead(Boolean(payload.mark_read_on_open));
      setDensity(payload.density || "comfortable");
    } catch (err) {
      if (err instanceof AuthError) onLost();
      else toasts.push({ text: err instanceof Error ? err.message : "chargement impossible", tone: "live" });
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    setSaving(true);
    try {
      await saveSettings({
        extra_prompt: prompt,
        signature,
        mark_read_on_open: markRead,
        density,
        ...(notionToken.trim() ? { notion_token: notionToken.trim() } : {}),
      });
      setNotionToken("");
      toasts.push({ text: "Réglages enregistrés", tone: "ok", duration: 2500 });
      await load();
      store.refresh("queue");
    } catch (err) {
      if (err instanceof AuthError) onLost();
      else toasts.push({ text: err instanceof Error ? err.message : "impossible", tone: "live" });
    } finally {
      setSaving(false);
    }
  }

  async function onProbe() {
    setProbing(true);
    await load(true);
    setProbing(false);
  }

  const imap = data?.imap;
  const imapTone: "ok" | "warn" | "off" = !data ? "warn" : imap?.ok ? "ok" : imap?.connected === false && !imap.error ? "warn" : imap ? "off" : "warn";
  const imapLine = !data
    ? "…"
    : !data.imap_ready
      ? "Non configuré"
      : imap?.ok
        ? `Connecté · ${imap.folder_count ?? "—"} dossiers · ${imap.inbox_messages ?? "—"} mails`
        : imap?.error || "Pas encore connecté (première relève à venir)";
  const claudeReady = Boolean(data?.llm_ready);
  const notionSet = Boolean(data?.notion_token_set || data?.notion_ready);
  const status = data?.notion_status;
  const notionTone: "ok" | "warn" | "off" = !notionSet ? "off" : status?.ok ? "ok" : "warn";
  const notionLine = !notionSet ? "Jeton manquant" : status === undefined || status === null ? "Jeton enregistré" : status.ok ? "Tâches + Projets accessibles" : status.view_only ? "Vue Radio Campus seulement" : "Tâches pas encore accessible";
  const worker = data?.worker;
  const index = store.index.indexed ? store.index : worker?.last_index || {};
  const jobs = worker?.jobs?.lanes || {};
  const running = Object.values(jobs).filter((lane) => lane.current).map((lane) => lane.current);
  const queued = Object.values(jobs).reduce((sum, lane) => sum + lane.queued_count, 0);
  const folders = data?.folders || {};

  return (
    <div className="settings">
      <form className="settings-col" onSubmit={onSubmit}>
        <header className="settings-hero">
          <div className="settings-hero-copy">
            <h1 className="settings-title">Réglages</h1>
            <p className="muted">Connexions, actions IMAP, consignes, signature. Aucun mail n’est envoyé d’ici.</p>
          </div>
          <Button variant="primary" busy={saving} onClick={(ev) => void onSubmit(ev as unknown as FormEvent)}>
            Enregistrer
          </Button>
        </header>

        <section className="settings-card">
          <p className="settings-kicker">État</p>
          <StatusRow label="IMAP" value={imapLine} tone={imapTone} />
          {data?.imap_user ? (
            <p className="settings-meta">
              {data.imap_user} · {data.imap_host}:{data.imap_port} ·{" "}
              <a className="settings-link" href={WEBMAIL} target="_blank" rel="noreferrer">
                Webmail
              </a>
              {imap?.last_ok ? ` · dernier échange ${agoLabel(imap.last_ok)}` : ""}
            </p>
          ) : null}
          <StatusRow
            label="Claude"
            value={!data ? "…" : claudeReady ? `Récap ${[modelLabel(data.anthropic_model), effortLabel(data.anthropic_effort)].filter(Boolean).join(" ")} · Tri ${modelLabel(data.anthropic_fast_model)}` : "Pas de clé"}
            tone={!data ? "warn" : claudeReady ? "ok" : "off"}
          />
          {data?.last_scan_usage ? <p className="settings-meta">Dernier relevé · {data.last_scan_usage}</p> : null}
          <StatusRow label="Notion" value={notionLine} tone={notionTone} detail={status?.detail || ""} />
          <div className="settings-actions">
            <Button variant="quiet" icon="refresh" busy={probing} onClick={() => void onProbe()}>
              Tester Notion
            </Button>
          </div>
        </section>

        <section className="settings-card">
          <p className="settings-kicker">Actions IMAP</p>
          <p className="muted">
            Archiver déplace le mail vers un dossier <strong>Inbox Zero</strong> visible dans le webmail. Lu et Drapeau changent les marqueurs IMAP.
            Tout est annulable depuis l’Historique. Rien n’est supprimé, rien n’est envoyé.
          </p>
          <div className="settings-chips">
            <Chip tone={imap?.can_move ? "ok" : "warn"} icon={imap?.can_move ? "check" : "close"}>
              {imap?.can_move ? "Déplacement possible" : imap ? "Pas de MOVE / UIDPLUS" : "Capacités inconnues"}
            </Chip>
            {(imap?.capabilities || []).map((cap) => (
              <Chip key={cap} tone="quiet">
                {cap}
              </Chip>
            ))}
          </div>
          <ul className="settings-folders">
            {Object.entries(folders).map(([key, path]) => (
              <li key={key}>
                <Icon name="archive" size={12} />
                <span className="settings-folder-cat">{data?.labels?.[key] || key}</span>
                <span className="settings-folder-path">{path}</span>
                {imap?.folders?.includes(path) ? <Chip tone="ok">créé</Chip> : <Chip tone="quiet">créé au premier archivage</Chip>}
              </li>
            ))}
          </ul>
          <Toggle
            checked={markRead}
            onChange={setMarkRead}
            label="Marquer lu à l’ouverture"
            hint="Après 1,5 s sur un mail non lu, le marqueur \\Seen est posé dans Roundcube. Désactivé : la lecture reste invisible (PEEK)."
          />
        </section>

        <section className="settings-card">
          <p className="settings-kicker">Index & relevés</p>
          <StatusRow
            label="Recherche"
            value={index.inbox && (index.indexed || 0) < index.inbox ? `${index.indexed} / ${index.inbox} mails indexés` : `${index.indexed || 0} mails indexés`}
            tone={index.inbox && (index.indexed || 0) < index.inbox ? "warn" : "ok"}
          />
          <StatusRow label="Tri Claude" value={`${data?.scan_days || 30} derniers jours · relevé toutes les 3 min`} tone="ok" />
          <StatusRow
            label="Travaux"
            value={running.length ? running.map((job) => `${job?.kind} ${job?.progress || ""}`.trim()).join(" · ") : queued ? `${queued} en attente` : "Rien en cours"}
            tone={running.length ? "warn" : "ok"}
          />
          {worker?.last_index_error ? <p className="settings-meta is-live">{worker.last_index_error}</p> : null}
        </section>

        <section className="settings-card">
          <p className="settings-kicker">Affichage</p>
          <div className="settings-radio" role="radiogroup" aria-label="Densité">
            {[
              ["comfortable", "Confortable"],
              ["compact", "Compacte"],
            ].map(([value, text]) => (
              <label key={value} className={`settings-radio-item${density === value ? " is-on" : ""}`}>
                <input type="radio" name="density" value={value} checked={density === value} onChange={() => setDensity(value)} />
                {text}
              </label>
            ))}
          </div>
        </section>

        <section className="settings-card">
          <p className="settings-kicker">Consignes</p>
          <p className="muted">Ajoutées au tri et aux récaps Claude. Ex. : les mails de la fédération vont dans À faire.</p>
          <textarea className="field" value={prompt} onChange={(ev) => setPrompt(ev.target.value)} rows={5} placeholder="Les mails de la fédération vont dans À faire." />
        </section>

        <section className="settings-card">
          <p className="settings-kicker">Signature</p>
          <p className="muted">Collée à la fin des exemples de réponse. À coller ensuite dans le webmail.</p>
          <textarea className="field" value={signature} onChange={(ev) => setSignature(ev.target.value)} rows={5} placeholder={"—\nPrénom\nRadio Campus Orléans 88.3"} />
        </section>

        <section className="settings-card">
          <p className="settings-kicker">Cowork</p>
          <p className="muted">
            File À faire / En attente en lecture seule. Markdown pour un dossier local Cowork, Atom pour un lecteur. Tailscale seulement. Cookie de session ou{" "}
            <code>Authorization: Bearer</code>, le jeton n’est jamais affiché ici.
          </p>
          <p className="settings-meta">
            <a className="settings-link" href={data?.feed_md || "/api/feed.md"} target="_blank" rel="noreferrer">
              {data?.feed_md || "/api/feed.md"}
            </a>
            {" · "}
            <a className="settings-link" href={data?.feed_atom || "/api/feed.atom"} target="_blank" rel="noreferrer">
              {data?.feed_atom || "/api/feed.atom"}
            </a>
          </p>
          <code className="settings-code">
            {`mkdir -p ~/Documents/RadioCampus/file-cowork && curl -o ~/Documents/RadioCampus/file-cowork/file.md -H 'Authorization: Bearer …' 'http://nasgul.taild4714f.ts.net:30128${data?.feed_md || "/api/feed.md"}'`}
          </code>
        </section>

        <section className="settings-card">
          <p className="settings-kicker">Notion</p>
          <p className="muted">
            {notionSet
              ? "Les tâches partent dans Tâches, État Programmé, projet Radio Campus, avec les pièces jointes du fil."
              : "Il faut une intégration interne, partagée avec les bases Tâches et Projets."}
          </p>
          <ol className="settings-steps">
            <li>
              Crée une intégration interne sur{" "}
              <a className="settings-link" href={NOTION_INTEGRATIONS} target="_blank" rel="noreferrer">
                notion.so/profile/integrations
              </a>
            </li>
            <li>
              Dans le panneau <strong>Nasgul</strong>, ajoute la{" "}
              <a className="settings-link" href={NOTION_TACHES} target="_blank" rel="noreferrer">
                Tâches
              </a>{" "}
              du workspace et{" "}
              <a className="settings-link" href={NOTION_PROJETS} target="_blank" rel="noreferrer">
                Projets
              </a>
              .
            </li>
            <li>Colle le jeton (ntn_…) et enregistre.</li>
          </ol>
          <input className="field" type="password" autoComplete="off" value={notionToken} onChange={(ev) => setNotionToken(ev.target.value)} placeholder={notionSet ? "Nouveau jeton (vide = inchangé)" : "ntn_…"} />
        </section>
      </form>
    </div>
  );
}
