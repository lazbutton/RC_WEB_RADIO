import type { SVGProps } from "react";

export type IconName = keyof typeof PATHS;

const PATHS = {
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
  user: "M20 21a8 8 0 1 0-16 0M12 13a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  users: "M17 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8",
  building: "M4 21V5l8-3 8 3v16M9 9h1M14 9h1M9 13h1M14 13h1M9 17h1M14 17h1M3 21h18",
  pin: "M12 22s7-6.5 7-12a7 7 0 1 0-14 0c0 5.5 7 12 7 12zM12 12a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
  calendar: "M4 5h16v16H4zM4 10h16M8 3v4M16 3v4",
  mic: "M12 15a4 4 0 0 0 4-4V6a4 4 0 0 0-8 0v5a4 4 0 0 0 4 4zM5 11a7 7 0 0 0 14 0M12 18v3M8 21h8",
  radio: "M4 9h16v11H4zM4 9l12-6M8 14a2 2 0 1 0 4 0 2 2 0 0 0-4 0M15 13h3M15 16h3",
  disc: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
  scissors: "M6 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM20 4L8.1 15.9M14.5 14.5L20 20M8.1 8.1L12 12",
  headphones: "M4 18v-6a8 8 0 0 1 16 0v6M4 14h3v6H4zM17 14h3v6h-3z",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  handshake: "M11 17l-4-4 3-3 4 4M7 13l-4-4 6-6 4 4M13 17l4 4 4-4-6-6M17 13l4-4-6-6",
  key: "M15 8a5 5 0 1 1-4.9 6L4 20v-3l6-6a5 5 0 0 1 5-3zM15 7h.01",
  file: "M14 3H6v18h12V7zM14 3v4h4M9 13h6M9 17h6",
  bell: "M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 21h4",
  home: "M3 11l9-8 9 8M5 10v10h14V10M10 20v-6h4v6",
  plus: "M12 5v14M5 12h14",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6",
  upload: "M12 16V4M6 10l6-6 6 6M4 20h16",
  chevron: "M9 6l6 6-6 6",
  clipboard: "M9 3h6v3H9zM6 5h12v16H6zM9 11h6M9 15h6",
  globe: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  wave: "M3 12h2l2-6 3 12 3-9 2 6 2-3h4",
  warning: "M12 3l10 18H2zM12 10v4M12 18h.01",
  "check-circle": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM8 12l3 3 5-6",
  lock: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17l9 5 9-5",
  send: "M22 2L11 13M22 2l-7 20-4-9-9-4z",
  star: "M12 3l2.8 6 6.2.7-4.6 4.3 1.3 6.3L12 17l-5.7 3.3 1.3-6.3L3 9.7 9.2 9z",
  download: "M12 4v12M6 10l6 6 6-6M4 20h16",
} as const satisfies Record<string, string>;

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
