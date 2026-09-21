import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

export function Chip({
  tone = "neutral",
  icon,
  children,
  title,
}: {
  tone?: "neutral" | "live" | "ok" | "warn" | "text" | "quiet";
  icon?: IconName;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span className={`chip is-${tone}`} title={title}>
      {icon ? <Icon name={icon} size={11} /> : null}
      {children}
    </span>
  );
}

export function catTone(category: string): "live" | "warn" | "text" | "quiet" | "neutral" {
  if (category === "todo") return "live";
  if (category === "waiting") return "warn";
  if (category === "read") return "text";
  if (category === "newsletters" || category === "spam") return "quiet";
  return "neutral";
}
