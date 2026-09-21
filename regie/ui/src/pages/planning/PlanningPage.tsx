import { useCallback, useEffect, useMemo, useState, type DragEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, qs, type LinkRow } from "../../api/client";
import { Button } from "../../components/Button";
import { Checkbox } from "../../components/Checkbox";
import { Empty, Field, Page, Pill, fmtDate } from "../../components/Page";
import { Sheet } from "../../components/Sheet";
import { useToasts } from "../../components/Toast";
import { EntityChip, useRegistry } from "../../lib/registry";
import { CommentsPanel } from "./CommentsPanel";
import type { Task } from "./TaskQuickAdd";

type User = { id: string; name: string };
const COLUMNS: { id: string; label: string }[] = [
  { id: "todo", label: "À faire" },
  { id: "doing", label: "En cours" },
  { id: "done", label: "Terminé" },
];

export function PlanningPage() {
  const [params, setParams] = useSearchParams();
  const reg = useRegistry();
  const toasts = useToasts();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [mine, setMine] = useState(false);
  const [q, setQ] = useState("");
  const [over, setOver] = useState<string | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const openId = params.get("task");
  const creating = params.get("new") === "1";

  const load = useCallback(() => {
    api.get<{ tasks: Task[]; users: User[] }>(`/api/v1/planning/tasks${qs({ assignee: mine ? reg.me?.user.id : undefined, q })}`).then((res) => {
      setTasks(res.tasks);
      setUsers(res.users);
    }).catch(() => undefined);
  }, [mine, q, reg.me]);

  useEffect(() => {
    const timer = window.setTimeout(load, q ? 150 : 0);
    const onEvent = (event: Event) => {
      const type = (event as CustomEvent<{ type: string }>).detail?.type || "";
      if (type.startsWith("task") || type === "tasks") load();
    };
    window.addEventListener("regie-event", onEvent);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("regie-event", onEvent);
    };
  }, [load, q]);

  const byStatus = useMemo(() => {
    const out: Record<string, Task[]> = { todo: [], doing: [], done: [] };
    for (const task of tasks) (out[task.status] ??= []).push(task);
    return out;
  }, [tasks]);
  const userName = (id: string) => users.find((u) => u.id === id)?.name || "?";

  async function drop(status: string, ev: DragEvent) {
    ev.preventDefault();
    setOver(null);
    const id = Number(ev.dataTransfer.getData("text/plain"));
    if (!id) return;
    const task = tasks.find((t) => t.id === id);
    if (!task || task.status === status) return;
    setTasks((prev) => prev.map((t) => (t.id === id ? { ...t, status } : t)));
    try {
      await api.patch(`/api/v1/planning/tasks/${id}`, { status });
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
      load();
    }
  }

  async function quickCreate() {
    if (!newTitle.trim()) return;
    await api.post("/api/v1/planning/tasks", { title: newTitle, assignees: mine && reg.me ? [reg.me.user.id] : [] });
    setNewTitle("");
    load();
  }

  return (
    <Page
      title="Planning"
      subtitle={`${tasks.filter((t) => t.status !== "done").length} tâches ouvertes`}
      wide
      actions={
        <>
          <Link to="/planning/week" className="btn btn-ghost">Semaine</Link>
          <Button variant="primary" icon="plus" onClick={() => setParams({ new: "1" })}>Nouvelle tâche</Button>
        </>
      }
    >
      <div className="toolbar">
        <input className="field" placeholder="Filtrer…" value={q} onChange={(ev) => setQ(ev.target.value)} style={{ maxWidth: 260 }} />
        <Checkbox checked={mine} onToggle={() => setMine((v) => !v)} label="Mes tâches" />
        <span className="spacer" />
        <input className="field" placeholder="Ajout rapide : titre puis Entrée" value={newTitle} onChange={(ev) => setNewTitle(ev.target.value)} onKeyDown={(ev) => ev.key === "Enter" && void quickCreate()} style={{ maxWidth: 360 }} />
      </div>
      <div className="kanban">
        {COLUMNS.map((column) => (
          <div key={column.id} className={`kanban-col${over === column.id ? " is-over" : ""}`} onDragOver={(ev) => { ev.preventDefault(); setOver(column.id); }} onDragLeave={() => setOver(null)} onDrop={(ev) => void drop(column.id, ev)}>
            <div className="kanban-head"><span>{column.label}</span><span>{byStatus[column.id]?.length || 0}</span></div>
            {(byStatus[column.id] || []).slice(0, column.id === "done" ? 30 : 200).map((task) => (
              <button key={task.id} type="button" className={`task-card${task.overdue ? " is-overdue" : ""}${String(task.id) === openId ? " is-on" : ""}`} draggable onDragStart={(ev) => ev.dataTransfer.setData("text/plain", String(task.id))} onClick={() => setParams({ task: String(task.id) })}>
                <span className="task-title">{task.title}</span>
                <span className="task-meta">
                  {task.due_at ? <span className={task.overdue ? "is-overdue-text" : ""}>{fmtDate(task.due_at, false)}</span> : null}
                  {task.checklist_total ? <span>{task.checklist_done}/{task.checklist_total}</span> : null}
                  {task.priority >= 2 ? <Pill tone="warn">urgent</Pill> : null}
                  {task.tags?.map((t) => <Pill key={t}>{t}</Pill>)}
                  <span className="spacer" />
                  {task.assignees.map((a) => <span key={a} className="avatar" title={userName(a)}>{userName(a).slice(0, 2).toUpperCase()}</span>)}
                </span>
              </button>
            ))}
            {!byStatus[column.id]?.length ? <Empty text="Vide." /> : null}
          </div>
        ))}
      </div>
      <Sheet open={Boolean(openId)} title="Tâche" onClose={() => setParams({})} wide>
        {openId ? <TaskDrawer id={Number(openId)} users={users} onChange={load} onClose={() => setParams({})} /> : null}
      </Sheet>
      <Sheet open={creating} title="Nouvelle tâche" onClose={() => setParams({})}>
        <TaskForm users={users} onDone={(task) => { setParams({ task: String(task.id) }); load(); }} />
      </Sheet>
    </Page>
  );
}

function TaskForm({ users, onDone }: { users: User[]; onDone: (task: Task) => void }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [due, setDue] = useState("");
  const [assignees, setAssignees] = useState<string[]>([]);
  const [checklist, setChecklist] = useState("");
  const [priority, setPriority] = useState(1);
  const toasts = useToasts();
  async function submit() {
    try {
      const res = await api.post<{ task: Task }>("/api/v1/planning/tasks", { title, description, priority, due_at: due ? new Date(due).toISOString() : null, assignees, checklist: checklist.split("\n").map((s) => s.trim()).filter(Boolean).map((text) => ({ text, done: false })) });
      onDone(res.task);
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }
  return (
    <div className="grid">
      <Field label="Titre"><input className="field" autoFocus value={title} onChange={(ev) => setTitle(ev.target.value)} /></Field>
      <Field label="Description"><textarea className="field" value={description} onChange={(ev) => setDescription(ev.target.value)} /></Field>
      <div className="form-row">
        <Field label="Échéance"><input className="field" type="datetime-local" value={due} onChange={(ev) => setDue(ev.target.value)} /></Field>
        <Field label="Priorité"><select className="field" value={priority} onChange={(ev) => setPriority(Number(ev.target.value))}><option value={0}>basse</option><option value={1}>normale</option><option value={2}>urgente</option></select></Field>
      </div>
      <Field label="Assigner à">
        <div className="chips">{users.map((u) => <Checkbox key={u.id} checked={assignees.includes(u.id)} onToggle={() => setAssignees((prev) => (prev.includes(u.id) ? prev.filter((x) => x !== u.id) : [...prev, u.id]))} label={u.name} />)}</div>
      </Field>
      <Field label="Checklist" hint="une ligne par étape"><textarea className="field" value={checklist} onChange={(ev) => setChecklist(ev.target.value)} /></Field>
      <div className="form-foot"><Button variant="primary" disabled={!title.trim()} onClick={() => void submit()}>Créer</Button></div>
    </div>
  );
}

function TaskDrawer({ id, users, onChange, onClose }: { id: number; users: User[]; onChange: () => void; onClose: () => void }) {
  const [task, setTask] = useState<Task | null>(null);
  const [links, setLinks] = useState<LinkRow[]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [newCheck, setNewCheck] = useState("");
  const toasts = useToasts();
  const load = useCallback(() => {
    api.get<{ task: Task; links: LinkRow[] }>(`/api/v1/planning/tasks/${id}`).then((res) => {
      setTask(res.task);
      setLinks(res.links);
      setTitle(res.task.title);
      setDescription(res.task.description || "");
    }).catch(() => setTask(null));
  }, [id]);
  useEffect(load, [load]);
  if (!task) return <Empty text="Tâche introuvable." />;

  async function patch(body: Record<string, unknown>) {
    try {
      const res = await api.patch<{ task: Task }>(`/api/v1/planning/tasks/${id}`, body);
      setTask(res.task);
      onChange();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }
  async function remove() {
    if (!window.confirm("Supprimer cette tâche ?")) return;
    await api.delete(`/api/v1/planning/tasks/${id}`);
    onChange();
    onClose();
  }
  const toggleAssignee = (uid: string) => void patch({ assignees: task.assignees.includes(uid) ? task.assignees.filter((x) => x !== uid) : [...task.assignees, uid] });

  return (
    <div className="grid">
      <input className="field task-title-input" value={title} onChange={(ev) => setTitle(ev.target.value)} onBlur={() => title !== task.title && void patch({ title })} />
      <div className="toolbar">
        {COLUMNS.map((c) => <Button key={c.id} variant={task.status === c.id ? "primary" : "ghost"} onClick={() => void patch({ status: c.id })}>{c.label}</Button>)}
        <span className="spacer" />
        <Button icon="trash" variant="danger" onClick={() => void remove()}>Supprimer</Button>
      </div>
      <div className="form-row">
        <Field label="Échéance"><input className="field" type="datetime-local" value={task.due_at ? toLocalInput(task.due_at) : ""} onChange={(ev) => void patch(ev.target.value ? { due_at: new Date(ev.target.value).toISOString() } : { clear_due: true })} /></Field>
        <Field label="Priorité"><select className="field" value={task.priority} onChange={(ev) => void patch({ priority: Number(ev.target.value) })}><option value={0}>basse</option><option value={1}>normale</option><option value={2}>urgente</option></select></Field>
      </div>
      <Field label="Assignés"><div className="chips">{users.map((u) => <Checkbox key={u.id} checked={task.assignees.includes(u.id)} onToggle={() => toggleAssignee(u.id)} label={u.name} />)}</div></Field>
      <Field label="Description"><textarea className="field" value={description} onChange={(ev) => setDescription(ev.target.value)} onBlur={() => description !== (task.description || "") && void patch({ description })} /></Field>
      <Field label={`Checklist ${task.checklist_done}/${task.checklist_total}`}>
        <ul className="checklist">
          {task.checklist.map((item, index) => (
            <li key={index} className={item.done ? "is-done" : ""}>
              <Checkbox checked={item.done} onToggle={() => void api.post<{ task: Task }>(`/api/v1/planning/tasks/${id}/check`, { index, done: !item.done }).then((res) => { setTask(res.task); onChange(); })} label="" />
              <span>{item.text}</span>
            </li>
          ))}
        </ul>
        <div className="toolbar">
          <input className="field" value={newCheck} onChange={(ev) => setNewCheck(ev.target.value)} placeholder="Ajouter une étape" onKeyDown={(ev) => { if (ev.key === "Enter" && newCheck.trim()) { void patch({ checklist: [...task.checklist, { text: newCheck.trim(), done: false }] }); setNewCheck(""); } }} />
        </div>
      </Field>
      {links.length ? <Field label="Lié à"><div className="chips">{links.map((l) => <EntityChip key={l.id} entity={l.other} />)}</div></Field> : null}
      <Field label="Commentaires"><CommentsPanel kind="task" id={id} /></Field>
    </div>
  );
}

function toLocalInput(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
