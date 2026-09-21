import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, qs } from "../../api/client";
import { Button } from "../../components/Button";
import { Card, Empty, Field, Page, Pill, Tabs } from "../../components/Page";
import { Sheet } from "../../components/Sheet";
import { useToasts } from "../../components/Toast";

type Kind = "person" | "organization" | "place";
type Row = Record<string, unknown> & { id: number; display_name?: string; name?: string; emails?: string[]; job_title?: string; kind?: string; city?: string; source?: string; tags?: string[] };
const LABELS: Record<Kind, { one: string; many: string; nameField: string }> = {
  person: { one: "Personne", many: "Personnes", nameField: "display_name" },
  organization: { one: "Structure", many: "Structures", nameField: "name" },
  place: { one: "Lieu", many: "Lieux", nameField: "name" },
};

export function ContactsPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const toasts = useToasts();
  const kind = (params.get("kind") as Kind) || "person";
  const [q, setQ] = useState(params.get("q") || "");
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [groups, setGroups] = useState<{ reason: string; key: string; ids: number[] }[]>([]);
  const [creating, setCreating] = useState<Kind | null>((params.get("new") as Kind) || null);
  const [vcf, setVcf] = useState(false);

  const load = useCallback(() => {
    api.get<{ items: Row[]; total: number }>(`/api/v1/contacts/${kind}${qs({ q, limit: 300 })}`).then((res) => {
      setRows(res.items);
      setTotal(res.total);
    }).catch(() => undefined);
    api.get<{ groups: { reason: string; key: string; ids: number[] }[] }>(`/api/v1/contacts/duplicates${qs({ kind })}`).then((res) => setGroups(res.groups)).catch(() => undefined);
  }, [kind, q]);

  useEffect(() => {
    const timer = window.setTimeout(load, q ? 150 : 0);
    return () => window.clearTimeout(timer);
  }, [load, q]);

  async function merge(group: { ids: number[] }) {
    const [keep, ...others] = group.ids;
    try {
      const res = await api.post<{ action: { id: number } }>("/api/v1/contacts/merge", { kind, keep, others });
      toasts.push({ text: "Fiches fusionnées", tone: "ok", action: { label: "Annuler", onClick: () => api.post(`/api/v1/actions/${res.action.id}/undo`).then(load) } });
      load();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }

  async function importSenders() {
    await api.post("/api/v1/contacts/import/senders");
    toasts.push({ text: "Import des expéditeurs fréquents lancé", tone: "ok" });
  }

  return (
    <Page
      title="Contacts"
      subtitle={`${total} ${LABELS[kind].many.toLowerCase()}`}
      actions={
        <>
          <Button icon="upload" onClick={() => setVcf(true)}>Importer des vCards</Button>
          <Button icon="download" onClick={() => window.open(`/api/v1/contacts/export.${kind === "person" ? "vcf" : "csv"}${kind === "person" ? "" : `?kind=${kind}`}`, "_blank")}>Exporter</Button>
          {kind === "person" ? <Button icon="mail" onClick={() => void importSenders()}>Depuis les expéditeurs</Button> : null}
          <Button variant="primary" icon="plus" onClick={() => setCreating(kind)}>Nouvelle {LABELS[kind].one.toLowerCase()}</Button>
        </>
      }
    >
      <div className="toolbar">
        <Tabs value={kind} onChange={(value) => setParams({ kind: value })} items={[{ id: "person" as Kind, label: "Personnes" }, { id: "organization" as Kind, label: "Structures" }, { id: "place" as Kind, label: "Lieux" }]} />
        <input className="field" placeholder="Chercher un nom, un e-mail…" value={q} onChange={(ev) => setQ(ev.target.value)} style={{ maxWidth: 320 }} />
      </div>
      {groups.length ? (
        <Card icon="layers" title={`${groups.length} doublon${groups.length > 1 ? "s" : ""} probable${groups.length > 1 ? "s" : ""}`}>
          <ul className="rows">
            {groups.map((group) => (
              <li key={`${group.reason}-${group.key}`}>
                <span className="small muted">{group.reason} · {group.key}</span>
                <span className="chips">{group.ids.map((id) => <Link key={id} to={`/contacts/${kind}/${id}`} className="entity-chip is-compact">#{id}</Link>)}</span>
                <span className="spacer" />
                <Button onClick={() => void merge(group)}>Fusionner (garder #{group.ids[0]})</Button>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
      {rows.length ? (
        <table className="table">
          <thead>
            <tr>
              <th>Nom</th>
              <th>{kind === "person" ? "E-mails" : kind === "organization" ? "Type" : "Ville"}</th>
              <th>{kind === "person" ? "Fonction" : "Site"}</th>
              <th>Source / tags</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="is-click" onClick={() => navigate(`/contacts/${kind}/${row.id}`)}>
                <td>{String(row[LABELS[kind].nameField] || "")}</td>
                <td className="muted">{kind === "person" ? (row.emails || []).join(", ") : kind === "organization" ? row.kind : row.city}</td>
                <td className="muted">{kind === "person" ? row.job_title : String(row.website || "")}</td>
                <td className="chips">{row.source ? <Pill tone="muted">{String(row.source)}</Pill> : null}{(row.tags || []).map((tag) => <Pill key={tag}>{tag}</Pill>)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty text={q ? "Rien ne correspond." : "Aucune fiche pour l’instant."} icon="users" />
      )}
      <Sheet open={creating !== null} title={`Nouvelle ${creating ? LABELS[creating].one.toLowerCase() : ""}`} onClose={() => setCreating(null)}>
        {creating ? <CreateForm kind={creating} onDone={(row) => { setCreating(null); navigate(`/contacts/${creating}/${row.id}`); }} /> : null}
      </Sheet>
      <Sheet open={vcf} title="Importer des vCards" onClose={() => setVcf(false)}>
        <VcfImport onDone={() => { setVcf(false); load(); }} />
      </Sheet>
    </Page>
  );
}

function CreateForm({ kind, onDone }: { kind: Kind; onDone: (row: Row) => void }) {
  const toasts = useToasts();
  const [data, setData] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const set = (key: string) => (ev: { target: { value: string } }) => setData((prev) => ({ ...prev, [key]: ev.target.value }));

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    setBusy(true);
    try {
      const payload: Record<string, unknown> = { ...data };
      if (data.emails) payload.emails = data.emails.split(/[,\s;]+/).filter(Boolean);
      if (data.tags) payload.tags = data.tags.split(/[,\s;]+/).filter(Boolean);
      const res = await api.post<{ entity: Row }>(`/api/v1/contacts/${kind}`, { data: payload });
      onDone(res.entity);
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid">
      {kind === "person" ? (
        <>
          <div className="form-row">
            <Field label="Prénom"><input className="field" value={data.first_name || ""} onChange={set("first_name")} /></Field>
            <Field label="Nom"><input className="field" value={data.last_name || ""} onChange={set("last_name")} /></Field>
          </div>
          <Field label="E-mails" hint="séparés par des virgules"><input className="field" value={data.emails || ""} onChange={set("emails")} /></Field>
          <Field label="Fonction"><input className="field" value={data.job_title || ""} onChange={set("job_title")} /></Field>
        </>
      ) : (
        <>
          <Field label="Nom"><input className="field" required value={data.name || ""} onChange={set("name")} /></Field>
          {kind === "organization" ? (
            <>
              <Field label="Type">
                <select className="field" value={data.kind || "autre"} onChange={set("kind")}>
                  {["asso", "salle", "label", "collectif", "institution", "media", "entreprise", "autre"].map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
              </Field>
              <Field label="E-mails" hint="les domaines servent à relier les mails automatiquement"><input className="field" value={data.emails || ""} onChange={set("emails")} /></Field>
              <Field label="Site"><input className="field" value={data.website || ""} onChange={set("website")} /></Field>
            </>
          ) : (
            <div className="form-row">
              <Field label="Adresse"><input className="field" value={data.address || ""} onChange={set("address")} /></Field>
              <Field label="Ville"><input className="field" value={data.city || "Orléans"} onChange={set("city")} /></Field>
            </div>
          )}
        </>
      )}
      <Field label="Tags"><input className="field" value={data.tags || ""} onChange={set("tags")} placeholder="equipe, presse…" /></Field>
      <Field label="Notes"><textarea className="field" value={data.notes || ""} onChange={set("notes")} /></Field>
      <div className="form-foot"><Button variant="primary" busy={busy} onClick={(ev) => void submit(ev as unknown as FormEvent)}>Créer</Button></div>
    </form>
  );
}

function VcfImport({ onDone }: { onDone: () => void }) {
  const toasts = useToasts();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    try {
      const res = await api.post<{ created: number; updated: number }>("/api/v1/contacts/import/vcards", { vcards: text });
      toasts.push({ text: `${res.created} créée(s), ${res.updated} mise(s) à jour`, tone: "ok" });
      onDone();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="grid">
      <p className="muted small">Exporte tes contacts Nextcloud (carnet « radiocampus ») en .vcf, puis colle le contenu ou dépose le fichier.</p>
      <input type="file" accept=".vcf,text/vcard" onChange={(ev) => { const file = ev.target.files?.[0]; if (file) void file.text().then(setText); }} />
      <textarea className="field" style={{ minHeight: 160 }} value={text} onChange={(ev) => setText(ev.target.value)} placeholder="BEGIN:VCARD…" />
      <div className="form-foot"><Button variant="primary" busy={busy} disabled={!text.trim()} onClick={() => void run()}>Importer</Button></div>
    </div>
  );
}
