export type CategoryId = string;

export type PadColor =
  | "amber"
  | "yellow"
  | "orange"
  | "red"
  | "pink"
  | "violet"
  | "blue"
  | "teal"
  | "green"
  | "lime"
  | "ice"
  | "white";

export type LibraryCategory = {
  id: CategoryId;
  title: string;
  color: PadColor;
  order: number;
};

export type LibrarySound = {
  soundId: string;
  title: string;
  durationSec: number;
  kind: CategoryId;
  color: PadColor;
  catalogId?: string;
  source?: "local" | "ntr";
};

export type LibraryFolder = {
  folderId: string;
  title: string;
  kind: CategoryId;
  color: PadColor;
  soundIds: string[];
};

export type PadAssign =
  | (LibrarySound & { mode: "one" })
  | {
      mode: "folder";
      folderId: string;
      title: string;
      kind: CategoryId;
      color: PadColor;
    };

export type PadMap = (PadAssign | null)[];
