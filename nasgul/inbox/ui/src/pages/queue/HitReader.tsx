import { useEffect, useState } from "react";
import { AuthError, getMail, type SearchHit } from "../../api";
import { Button } from "../../components/Button";
import { Chip, catTone } from "../../components/Chip";
import { Skeleton } from "../../components/Skeleton";
import { useAuth } from "../../auth";
import { absoluteStamp, chipName, folderShort } from "../../lib/format";
import { recapWithLinks } from "../../lib/links";
import { highlight } from "./HitRow";

export function HitReader({
  hit,
  q,
  labels,
  onAdopt,
  busy,
}: {
  hit: SearchHit;
  q: string;
  labels: Record<string, string>;
  onAdopt: (queue: boolean) => void;
  busy: boolean;
}) {
  const { onLost } = useAuth();
  const [body, setBody] = useState<string | null>(null);
  useEffect(() => {
    setBody(null);
    let alive = true;
    getMail(hit.id)
      .then((data) => alive && setBody(data.body || hit.excerpt))
      .catch((err) => {
        if (err instanceof AuthError) onLost();
        else if (alive) setBody(hit.excerpt);
      });
    return () => {
      alive = false;
    };
  }, [hit.id, hit.excerpt, onLost]);
  return (
    <div className="reader" key={`hit-${hit.id}`}>
      <header className="reader-head">
        <h1 className="reader-title">{highlight(hit.subject, q)}</h1>
        <p className="reader-meta">
          <span className="reader-people">{chipName(hit.sender_name)}</span>
          {hit.category ? <Chip tone={catTone(hit.category)}>{labels[hit.category] || hit.category}</Chip> : <Chip tone="quiet">Hors des 30 jours</Chip>}
          {hit.folder && hit.folder !== "INBOX" ? <Chip tone="quiet" icon="archive">{folderShort(hit.folder)}</Chip> : null}
          <span className="reader-count">{absoluteStamp(hit.created_rel)}</span>
        </p>
        <div className="action-bar" role="toolbar" aria-label="Actions sur ce mail">
          <Button variant="quiet" icon="sparkle" busy={busy} onClick={() => onAdopt(false)} tip="Ajoute le mail à Inbox Zero et lance un récap Claude">
            Récap
          </Button>
          <Button variant="quiet" icon="inbox" busy={busy} onClick={() => onAdopt(true)} tip="Le mail rejoint la file À trier">
            Mettre à trier
          </Button>
        </div>
      </header>
      <div className="reader-scroll">
        <div className="messages">
          <article className="message is-last">
            <header className="message-head">
              <span className="message-from">{chipName(hit.sender_name)}</span>
              <span className="message-email">{hit.sender}</span>
            </header>
            {body === null ? <Skeleton lines={4} /> : <div className="message-body">{recapWithLinks(body)}</div>}
          </article>
        </div>
      </div>
    </div>
  );
}
