export function ProgressLine({ active, label }: { active: boolean; label?: string }) {
  return (
    <div className={`progress-line${active ? " is-active" : ""}`} role="progressbar" aria-busy={active} aria-label={label || "En cours"}>
      <span className="progress-line-bar" />
    </div>
  );
}
