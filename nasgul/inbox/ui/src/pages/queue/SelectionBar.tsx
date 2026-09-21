import type { ActionKind } from "../../api";
import { Button } from "../../components/Button";

export function SelectionBar({
  count,
  canMove,
  onAction,
  onClear,
  onAll,
}: {
  count: number;
  canMove: boolean;
  onAction: (kind: ActionKind) => void;
  onClear: () => void;
  onAll: () => void;
}) {
  return (
    <div className="selection-bar" role="toolbar" aria-label="Actions sur la sélection">
      <span className="selection-count">{count} sélectionné{count > 1 ? "s" : ""}</span>
      <div className="selection-actions">
        {canMove ? (
          <Button variant="quiet" icon="archive" shortcut="⇧E" onClick={() => onAction("archive")}>
            Archiver
          </Button>
        ) : null}
        <Button variant="quiet" icon="mail-open" onClick={() => onAction("seen")}>
          Lu
        </Button>
        <Button variant="quiet" icon="flag" onClick={() => onAction("flag")}>
          Drapeau
        </Button>
        <Button variant="quiet" icon="clock" onClick={() => onAction("later")}>
          Plus tard
        </Button>
      </div>
      <div className="selection-tail">
        <Button variant="quiet" onClick={onAll}>
          Tout
        </Button>
        <Button variant="quiet" icon="close" onClick={onClear}>
          Aucun
        </Button>
      </div>
    </div>
  );
}
