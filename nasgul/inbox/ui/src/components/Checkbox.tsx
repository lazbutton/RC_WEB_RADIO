import type { MouseEvent } from "react";
import { Icon } from "./Icon";

export function Checkbox({
  checked,
  onToggle,
  label,
  className = "",
}: {
  checked: boolean;
  onToggle: (ev: MouseEvent) => void;
  label: string;
  className?: string;
}) {
  return (
    <span
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      tabIndex={-1}
      className={`checkbox${checked ? " is-on" : ""} ${className}`}
      onClick={(ev) => {
        ev.stopPropagation();
        onToggle(ev);
      }}
    >
      <Icon name="check" size={11} />
    </span>
  );
}
