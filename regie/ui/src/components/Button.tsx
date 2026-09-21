import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Icon, type IconName } from "./Icon";
import { Kbd } from "./Kbd";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "ghost" | "primary" | "danger" | "quiet" | "icon";
  icon?: IconName;
  shortcut?: string;
  busy?: boolean;
  tip?: string;
  children?: ReactNode;
};

export function Button({ variant = "ghost", icon, shortcut, busy, tip, className, children, disabled, ...rest }: Props) {
  const classes = ["btn", `btn-${variant}`, busy ? "is-busy" : "", className || ""].filter(Boolean).join(" ");
  return (
    <button
      type="button"
      className={classes}
      disabled={disabled || busy}
      data-key={shortcut}
      data-tip={tip}
      aria-label={variant === "icon" && typeof children === "string" ? children : rest["aria-label"]}
      {...rest}
    >
      {busy ? <span className="spinner" aria-hidden /> : icon ? <Icon name={icon} /> : null}
      {variant === "icon" ? null : children ? <span className="btn-label">{children}</span> : null}
      {shortcut && variant !== "icon" ? <Kbd>{shortcut}</Kbd> : null}
    </button>
  );
}
