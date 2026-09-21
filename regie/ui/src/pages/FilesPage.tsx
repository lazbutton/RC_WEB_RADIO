import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { filesList } from "../api/client";
import { Icon } from "../components/Icon";
import { Empty, Page, Pill, fmtDate } from "../components/Page";
import { formatSize } from "../lib/format";

export function FilesPage() {
  const [params, setParams] = useSearchParams();
  const path = params.get("path") || "";
  const [data, setData] = useState<Awaited<ReturnType<typeof filesList>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setError(null);
    filesList(path).then(setData).catch((err) => setError(err instanceof Error ? err.message : "erreur"));
  }, [path]);
  const crumbs = path.split("/").filter(Boolean);
  return (
    <Page title="Fichiers" subtitle="Médiathèque BUTTON-Media sur le NAS. Lecture partout, écriture seulement dans les dossiers autorisés (00-inbox, 40-emissions). Jamais de suppression." wide>
      <div className="crumbs">
        <button type="button" onClick={() => setParams({})}>BUTTON-Media</button>
        {crumbs.map((part, index) => (
          <span key={index}> / <button type="button" onClick={() => setParams({ path: crumbs.slice(0, index + 1).join("/") })}>{part}</button></span>
        ))}
        {data?.writable ? <Pill tone="ok">inscriptible</Pill> : null}
      </div>
      {error ? <Empty text={error} icon="warning" /> : null}
      {data ? (
        <div>
          {data.dirs.map((dir) => (
            <button key={dir.path} type="button" className="file-row" style={{ width: "100%", textAlign: "left", background: "none", border: 0, font: "inherit", cursor: "pointer" }} onClick={() => setParams({ path: dir.path })}>
              <Icon name="chevron" size={14} /><span>{dir.name}</span><span /><span className="small muted">{dir.writable ? "écriture" : ""}</span>
            </button>
          ))}
          {data.files.map((file) => (
            <div key={file.path} className="file-row">
              <Icon name={file.kind === "audio" ? "headphones" : file.kind === "image" ? "eye" : "file"} size={14} />
              <a href={`/api/v1/files/raw?path=${encodeURIComponent(file.path)}`} target="_blank" rel="noreferrer">{file.name}</a>
              <span className="small muted">{formatSize(file.size)}</span>
              <span className="small muted">{fmtDate(file.modified_at)}</span>
            </div>
          ))}
          {!data.dirs.length && !data.files.length ? <Empty text="Dossier vide." icon="file" /> : null}
        </div>
      ) : null}
    </Page>
  );
}
