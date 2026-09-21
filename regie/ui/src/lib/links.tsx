import type { ReactNode } from "react";

const TOKEN_RE =
  /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|<(https?:\/\/[^>\s]+)>|https?:\/\/[^\s<>"'\]\)]+/gi;

function cleanHref(raw: string): string {
  return raw.replace(/[.,;:!?]+$/, "");
}

export function prettyLink(href: string): string {
  try {
    const url = new URL(href);
    const host = url.hostname.replace(/^www\./, "");
    let path = decodeURIComponent(url.pathname).replace(/\/$/, "");
    if (path.length > 36) path = `${path.slice(0, 34)}…`;
    return path ? `${host}${path}` : host;
  } catch {
    return href;
  }
}

function linkNode(href: string, label: string, key: string): ReactNode {
  const safe = href.startsWith("https://") || href.startsWith("http://");
  if (!safe) return label;
  return (
    <a key={key} className="recap-link" href={href} target="_blank" rel="noreferrer">
      {label}
    </a>
  );
}

export function recapWithLinks(text: string): ReactNode {
  const value = text || "";
  const nodes: ReactNode[] = [];
  const re = new RegExp(TOKEN_RE);
  let last = 0;
  let index = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(value))) {
    if (match.index > last) nodes.push(value.slice(last, match.index));
    const href = cleanHref(match[2] || match[3] || match[0]);
    const label = match[1] || prettyLink(href);
    nodes.push(linkNode(href, label, `l${index}`));
    index += 1;
    last = match.index + match[0].length;
  }
  if (last < value.length) nodes.push(value.slice(last));
  return nodes.length ? nodes : value;
}
