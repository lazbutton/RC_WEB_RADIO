import type { ActionKind } from "../../api";
import { Button } from "../../components/Button";
import type { Thread } from "../../lib/threads";

export function ActionBar({
  thread,
  canMove,
  history,
  onAction,
  onNotion,
  onCopy,
  hasDraft,
  copied,
  notionBusy,
  notionUrl,
}: {
  thread: Thread;
  canMove: boolean;
  history: boolean;
  onAction: (kind: ActionKind) => void;
  onNotion: () => void;
  onCopy: () => void;
  hasDraft: boolean;
  copied: boolean;
  notionBusy: boolean;
  notionUrl: string;
}) {
  const status = thread.latest.status;
  return (
    <div className="action-bar" role="toolbar" aria-label="Actions sur ce mail">
      {!history ? (
        <>
          {canMove ? (
            <Button variant="quiet" icon="archive" shortcut="e" tip="Déplacer vers Inbox Zero / catégorie" onClick={() => onAction("archive")}>
              Archiver
            </Button>
          ) : null}
          <Button
            variant="quiet"
            icon={thread.unseen ? "mail-open" : "mail"}
            shortcut="u"
            tip={thread.unseen ? "Marquer lu dans Roundcube" : "Marquer non lu dans Roundcube"}
            onClick={() => onAction(thread.unseen ? "seen" : "unseen")}
          >
            {thread.unseen ? "Lu" : "Non lu"}
          </Button>
          <Button
            variant="quiet"
            icon={thread.flagged ? "flag-filled" : "flag"}
            shortcut="s"
            className={thread.flagged ? "is-flagged" : ""}
            tip={thread.flagged ? "Retirer le drapeau" : "Poser un drapeau"}
            onClick={() => onAction(thread.flagged ? "unflag" : "flag")}
          >
            Drapeau
          </Button>
          <Button variant="quiet" icon="clock" shortcut="l" tip="Sort de la file, reste dans la boîte" onClick={() => onAction("later")}>
            Plus tard
          </Button>
        </>
      ) : (
        <>
          {status === "moved" && canMove ? (
            <Button variant="quiet" icon="undo" shortcut="r" tip="Remettre dans la boîte" onClick={() => onAction("restore")}>
              Remettre
            </Button>
          ) : null}
          {status === "skipped" ? (
            <Button variant="quiet" icon="undo" shortcut="r" tip="Remettre à trier" onClick={() => onAction("unlater")}>
              À trier
            </Button>
          ) : null}
          {status === "skipped" && canMove ? (
            <Button variant="quiet" icon="archive" shortcut="e" onClick={() => onAction("archive")}>
              Archiver
            </Button>
          ) : null}
        </>
      )}
      <span className="action-gap" />
      {hasDraft ? (
        <Button variant="quiet" icon={copied ? "check" : "copy"} shortcut="c" className={copied ? "is-done" : ""} onClick={onCopy}>
          {copied ? "Copié" : "Copier"}
        </Button>
      ) : null}
      {notionUrl ? (
        <a className="btn btn-quiet" href={notionUrl} target="_blank" rel="noreferrer" data-key="n">
          <span className="btn-label">Ouvrir dans Notion</span>
        </a>
      ) : (
        <Button variant="quiet" icon="notion" shortcut="n" busy={notionBusy} onClick={onNotion} tip="Créer la tâche Notion avec les pièces">
          Notion
        </Button>
      )}
    </div>
  );
}
