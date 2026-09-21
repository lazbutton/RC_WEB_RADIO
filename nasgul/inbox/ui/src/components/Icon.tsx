import type { SVGProps } from "react";

export type IconName =
  | "archive"
  | "mail-open"
  | "mail"
  | "flag"
  | "flag-filled"
  | "clock"
  | "notion"
  | "paperclip"
  | "search"
  | "undo"
  | "check"
  | "arrow-left"
  | "arrow-right"
  | "refresh"
  | "copy"
  | "play"
  | "external"
  | "sparkle"
  | "close"
  | "keyboard"
  | "history"
  | "inbox"
  | "settings"
  | "dot";

const PATHS: Record<IconName, string> = {
  archive: "M3 7h18M5 7v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7M4 4h16v3H4zM10 12h4",
  "mail-open": "M3 10l9-6 9 6v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM3 10l9 6 9-6",
  mail: "M4 5h16v14H4zM4 6l8 7 8-7",
  flag: "M5 21V4M5 4h11l-1.5 4L16 12H5",
  "flag-filled": "M5 21V4M5 4h11l-1.5 4L16 12H5",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  notion: "M6 4h9l4 4v12H6zM9 9h6M9 13h6M9 17h4",
  paperclip: "M21 12.5l-8.2 8.2a5 5 0 0 1-7-7l9-9a3.2 3.2 0 0 1 4.5 4.5l-9 9a1.5 1.5 0 0 1-2-2l8-8",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  undo: "M9 14l-4-4 4-4M5 10h9a5 5 0 0 1 0 10h-2",
  check: "M5 12l5 5L20 7",
  "arrow-left": "M19 12H5M11 18l-6-6 6-6",
  "arrow-right": "M5 12h14M13 6l6 6-6 6",
  refresh: "M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5",
  copy: "M9 9h11v11H9zM4 15V4h11",
  play: "M7 5l12 7-12 7z",
  external: "M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5",
  sparkle: "M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2zM19 15l1 2 2 1-2 1-1 2-1-2-2-1 2-1z",
  close: "M6 6l12 12M18 6L6 18",
  keyboard: "M3 7h18v10H3zM7 11h1M11 11h1M15 11h1M7 14h10",
  history: "M4 12a8 8 0 1 1 2.3 5.7M4 12V7M4 12h5M12 8v4l3 2",
  inbox: "M3 13h5l2 3h4l2-3h5M5 5h14l2 8v6H3v-6z",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12l2-1-1-3-2 .3a7 7 0 0 0-1.5-1.5L17 4l-3-1-1 2a7 7 0 0 0-2 0L10 3 7 4l.5 2.8A7 7 0 0 0 6 8.3L4 8l-1 3 2 1a7 7 0 0 0 0 2l-2 1 1 3 2-.3a7 7 0 0 0 1.5 1.5L7 20l3 1 1-2a7 7 0 0 0 2 0l1 2 3-1-.5-2.8a7 7 0 0 0 1.5-1.5l2 .3 1-3-2-1a7 7 0 0 0 0-2z",
  dot: "M12 12h.01",
};

export function Icon({ name, size = 16, className, ...rest }: { name: IconName; size?: number } & SVGProps<SVGSVGElement>) {
  const filled = name === "flag-filled";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={["icon", className].filter(Boolean).join(" ")}
      {...rest}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
