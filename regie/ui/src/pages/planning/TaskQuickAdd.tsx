import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, qs } from "../../api/client";
import { Button } from "../../components/Button";
import { Pill, fmtDate } from "../../components/Page";

export type Task = { id: number; title: string; status: string; due_at: string | null; overdue: boolean; assignees: string[]; checklist_done: number; checklist_total: number; priority: number; description: string; checklist: { text: string; done: boolean }[]; tags: string[] };

/** Onglet Tâches d'une entité : liste des tâches liées + ajout en une ligne. */
export function TaskQuickAdd({ kind, id }: { kind: string; id: string | number }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [title, setTitle] = useState("");
  const load = useCallback(() => {
    api.get<{ tasks: Task[] }>(`/api/v1/planning/tasks${qs({ entity_kind: kind, entity_id: String(id) })}`).then((res) => setTasks(res.tasks)).catch(() => undefined);
  }, [kind, id]);
  useEffect(load, [load]);

  async function add() {
    if (!title.trim()) return;
    await api.post("/api/v1/planning/tasks", { title, link_kind: kind, link_id: String(id) });
    setTitle("");
    load();
  }

  return (
    <div className="grid">
      {tasks.length ? (
        <ul className="rows">
          {tasks.map((task) => (
            <li key={task.id}>
              <Pill tone={task.status === "done" ? "ok" : task.overdue ? "warn" : "muted"}>{task.status}</Pill>
              <Link to={`/planning?task=${task.id}`}>{task.title}</Link>
              <span className="spacer" />
              {task.due_at ? <span className="small muted">{fmtDate(task.due_at, false)}</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">Aucune tâche liée.</p>
      )}
      <div className="toolbar">
        <input className="field" value={title} onChange={(ev) => setTitle(ev.target.value)} placeholder="Nouvelle tâche liée à cette fiche…" onKeyDown={(ev) => ev.key === "Enter" && void add()} />
        <Button icon="plus" onClick={() => void add()} disabled={!title.trim()}>Ajouter</Button>
      </div>
    </div>
  );
}
