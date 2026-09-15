import type { CatalogItem } from "../mock/catalog";

export function TrackCopy({
  item,
  size = "list",
}: {
  item: CatalogItem;
  size?: "now" | "list";
}) {
  const primary = item.artist ?? item.title;
  const secondary = item.artist ? item.title : (item.cart ?? null);

  return (
    <span className={`track-copy is-${size}`}>
      <span className="track-primary">{primary}</span>
      {secondary ? <span className="track-secondary">{secondary}</span> : null}
    </span>
  );
}
