import { ATELIER_LINK, type Atelier } from "./lib/urls";

type Props = {
  name: string;
  clock: string;
  source: string;
  sourceReady: boolean;
  ateliers: Atelier[];
  onCommand: () => void;
};

export function Rail({ name, clock, source, sourceReady, ateliers, onCommand }: Props) {
  return (
    <aside className="rail">
      <p className="rail-mark">{name}</p>
      <p className="rail-clock" aria-label="Heure à Paris">
        {clock}
      </p>
      {sourceReady ? <p className="rail-source">{source}</p> : <p className="skel skel-source" />}
      <nav className="rail-nav" aria-label="Ateliers">
        {ateliers.map((item) => (
          <a key={item.href} href={item.href} {...ATELIER_LINK}>
            {item.title}
          </a>
        ))}
      </nav>
      <button type="button" className="rail-dock" onClick={onCommand}>
        Ateliers
        <kbd>⌘K</kbd>
      </button>
    </aside>
  );
}
