import { isAudioFile } from "./kind";

export type CollectedDrop = {
  files: File[];
  folderName: string | null;
};

type Entry = {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  file?: (ok: (file: File) => void, err?: (error: DOMException) => void) => void;
  createReader?: () => DirectoryReader;
};

type DirectoryReader = {
  readEntries: (ok: (entries: Entry[]) => void, err?: (error: DOMException) => void) => void;
};

export function folderNameFromFiles(files: File[]): string | null {
  const rel = files.map((file) => file.webkitRelativePath).filter(Boolean);
  if (!rel.length) return null;
  const top = rel[0]?.split("/")[0];
  if (!top) return null;
  if (!rel.every((path) => path.split("/")[0] === top)) return null;
  return top;
}

async function readAllEntries(reader: DirectoryReader): Promise<Entry[]> {
  const out: Entry[] = [];
  for (;;) {
    const batch = await new Promise<Entry[]>((resolve, reject) => {
      reader.readEntries(resolve, (error) => reject(error));
    });
    if (!batch.length) break;
    out.push(...batch);
  }
  return out;
}

async function filesFromEntry(entry: Entry): Promise<File[]> {
  if (entry.isFile) {
    if (!entry.file) return [];
    const file = await new Promise<File>((resolve, reject) => {
      entry.file!(resolve, (error) => reject(error));
    });
    return isAudioFile(file) ? [file] : [];
  }
  if (!entry.isDirectory || !entry.createReader) return [];
  const children = await readAllEntries(entry.createReader());
  const nested = await Promise.all(children.map(filesFromEntry));
  return nested.flat();
}

export async function collectDroppedFiles(event: { dataTransfer: DataTransfer | null }): Promise<CollectedDrop> {
  const transfer = event.dataTransfer;
  if (!transfer) return { files: [], folderName: null };

  const entries: Entry[] = [];
  for (const item of transfer.items) {
    const entry = item.webkitGetAsEntry?.() as Entry | null;
    if (entry) entries.push(entry);
  }

  if (entries.length) {
    const files = (await Promise.all(entries.map(filesFromEntry))).flat();
    if (files.length) {
      const folderName =
        entries.length === 1 && entries[0]?.isDirectory ? entries[0].name : folderNameFromFiles(files);
      return { files, folderName };
    }
  }

  const files = Array.from(transfer.files).filter(isAudioFile);
  return { files, folderName: folderNameFromFiles(files) };
}
