import { useCallback, useEffect, useState } from "react";
import { api, qs } from "../../api/client";
import { Button } from "../../components/Button";
import { fmtDate } from "../../components/Page";

type Comment = { id: number; body: string; author_name: string | null; created_at: string };

export function CommentsPanel({ kind, id }: { kind: string; id: string | number }) {
  const [rows, setRows] = useState<Comment[]>([]);
  const [text, setText] = useState("");
  const load = useCallback(() => {
    api.get<{ comments: Comment[] }>(`/api/v1/planning/comments${qs({ entity_kind: kind, entity_id: String(id) })}`).then((res) => setRows(res.comments)).catch(() => undefined);
  }, [kind, id]);
  useEffect(load, [load]);

  async function send() {
    if (!text.trim()) return;
    await api.post("/api/v1/planning/comments", { entity_kind: kind, entity_id: String(id), body: text });
    setText("");
    load();
  }

  return (
    <div className="grid">
      {rows.length ? (
        <ul className="rows">
          {rows.map((row) => (
            <li key={row.id} style={{ flexDirection: "column", alignItems: "stretch", gap: 2 }}>
              <span className="small muted">{row.author_name || "?"} · {fmtDate(row.created_at)}</span>
              <span style={{ whiteSpace: "pre-wrap" }}>{row.body}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">Pas encore de commentaire.</p>
      )}
      <div className="toolbar">
        <input className="field" value={text} onChange={(ev) => setText(ev.target.value)} placeholder="Écrire à l’équipe…" onKeyDown={(ev) => ev.key === "Enter" && !ev.shiftKey && void send()} />
        <Button icon="send" onClick={() => void send()} disabled={!text.trim()}>Envoyer</Button>
      </div>
    </div>
  );
}
