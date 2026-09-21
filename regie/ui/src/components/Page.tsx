import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

export function Page({ title, subtitle, actions, children, wide = false }: { title: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <section className={`page${wide ? " is-wide" : ""}`}>
      <header className="page-head">
        <div>
          <h1 className="page-title">{title}</h1>
          {subtitle ? <p className="page-sub">{subtitle}</p> : null}
        </div>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </header>
      {children}
    </section>
  );
}

export function Card({ title, icon, children, actions, className = "" }: { title?: ReactNode; icon?: IconName; children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <div className={`card ${className}`.trim()}>
      {title || actions ? (
        <div className="card-head">
          <h2 className="card-title">
            {icon ? <Icon name={icon} size={14} /> : null}
            {title}
          </h2>
          {actions ? <div className="card-actions">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="fld">
      <span className="fld-label">{label}</span>
      {children}
      {hint ? <span className="fld-hint">{hint}</span> : null}
    </label>
  );
}

export function Empty({ text, icon = "dot" }: { text: string; icon?: IconName }) {
  return (
    <p className="empty-row">
      <Icon name={icon} size={14} /> {text}
    </p>
  );
}

export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: "warn" | "ok" | "muted" }) {
  return (
    <div className={`stat${tone ? ` is-${tone}` : ""}`}>
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

export function Pill({ children, tone = "" }: { children: ReactNode; tone?: string }) {
  return <span className={`pill${tone ? ` is-${tone}` : ""}`}>{children}</span>;
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (value: T) => void; items: { id: T; label: string; count?: number }[] }) {
  return (
    <div className="tabs" role="tablist">
      {items.map((item) => (
        <button key={item.id} type="button" role="tab" aria-selected={value === item.id} className={`tab${value === item.id ? " is-on" : ""}`} onClick={() => onChange(item.id)}>
          {item.label}
          {item.count ? <span className="tab-count">{item.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function fmtDate(value?: string | null, withTime = true): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
  const day = date.toLocaleDateString("fr-FR", { weekday: "short", day: "2-digit", month: "short" });
  if (!withTime) return day;
  return `${day} ${date.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`;
}

export function fmtDuration(seconds?: number | null): string {
  if (!seconds && seconds !== 0) return "";
  const total = Math.round(Number(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}h${String(m).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
