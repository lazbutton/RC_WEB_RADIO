export function Skeleton({ lines = 3, className = "" }: { lines?: number; className?: string }) {
  return (
    <div className={`skeleton ${className}`} aria-hidden>
      {Array.from({ length: lines }).map((_, index) => (
        <span key={index} className="skeleton-line" style={{ width: `${index === lines - 1 ? 58 : 92 - index * 9}%` }} />
      ))}
    </div>
  );
}

export function RowSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="skeleton-rows" aria-hidden>
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="skeleton-row">
          <span className="skeleton-dot" />
          <div className="skeleton-col">
            <span className="skeleton-line" style={{ width: `${45 + (index % 3) * 12}%` }} />
            <span className="skeleton-line" style={{ width: `${70 + (index % 2) * 15}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}
