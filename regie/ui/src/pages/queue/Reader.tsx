import type { ReactNode } from "react";
import type { MailItem } from "../../api";
import { Chip, catTone } from "../../components/Chip";
import { Icon } from "../../components/Icon";
import { absoluteStamp, chipName, folderShort, formatSize, relativeShort } from "../../lib/format";
import { recapWithLinks } from "../../lib/links";
import { bubbleText, type Thread } from "../../lib/threads";

export function Reader({
  thread,
  labels,
  history,
  toolbar,
  children,
}: {
  thread: Thread;
  labels: Record<string, string>;
  history: boolean;
  toolbar: ReactNode;
  children?: ReactNode;
}) {
  const label = labels[thread.category] || thread.category;
  const status = thread.latest.status;
  const folder = thread.latest.folder;
  return (
    <div className="reader">
      <header className="reader-head">
        <div className="reader-title-row">
          <h1 className="reader-title" tabIndex={-1}>
            {thread.title}
          </h1>
        </div>
        <p className="reader-meta">
          <span className="reader-people">{thread.people.map((name) => chipName(name)).join(" · ")}</span>
          <Chip tone={catTone(thread.category)}>{label}</Chip>
          {thread.unseen ? <Chip tone="text">Non lu</Chip> : null}
          {thread.flagged ? (
            <Chip tone="warn" icon="flag-filled">
              Drapeau
            </Chip>
          ) : null}
          {thread.pieces.length ? (
            <Chip tone="text" icon="paperclip">
              {thread.pieces.length} PJ
            </Chip>
          ) : null}
          {thread.notionUrl ? (
            <Chip tone="ok" icon="notion">
              Notion
            </Chip>
          ) : null}
          {history && status === "moved" ? (
            <Chip tone="quiet" icon="archive">
              {folderShort(folder)}
            </Chip>
          ) : null}
          {history && status === "skipped" ? (
            <Chip tone="quiet" icon="clock">
              Plus tard
            </Chip>
          ) : null}
          {thread.items.length > 1 ? <span className="reader-count">{thread.items.length} messages</span> : null}
        </p>
        {toolbar}
      </header>
      <div className="reader-scroll">
        <div className="messages">
          {thread.items.map((item, index) => (
            <Message key={item.id} item={item} last={index === thread.items.length - 1} />
          ))}
        </div>
        {children}
      </div>
    </div>
  );
}

function Message({ item, last }: { item: MailItem; last: boolean }) {
  const body = bubbleText(item) || "…";
  const pieces = item.attachments || [];
  return (
    <article className={`message${last ? " is-last" : ""}${item.seen === false ? " is-unseen" : ""}`}>
      <header className="message-head">
        <span className="message-from">{chipName(item.sender_name)}</span>
        <span className="message-email">{item.sender_email}</span>
        <time className="message-when" title={absoluteStamp(item.created_rel)} dateTime={item.mailed_at || undefined}>
          {relativeShort(item.created_rel)}
        </time>
      </header>
      <div className="message-body">{recapWithLinks(body)}</div>
      {pieces.length ? (
        <ul className="message-pieces" aria-label="Pièces jointes">
          {pieces.map((piece) => (
            <li key={piece.n} className={`piece-chip is-${piece.kind}`} title={piece.filename}>
              <Icon name="paperclip" size={11} />
              <span className="piece-chip-name">{piece.filename}</span>
              {formatSize(piece.size) ? <span className="piece-chip-size">{formatSize(piece.size)}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  );
}
