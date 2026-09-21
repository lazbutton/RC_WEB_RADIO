import { useEffect, useMemo, useRef, useState } from "react";
import { AuthError, searchMails, type SearchHit } from "../api";
import { useAuth } from "../auth";
import { Icon, type IconName } from "./Icon";
import { Kbd } from "./Kbd";
import { Sheet } from "./Sheet";

export type Command = {
  id: string;
  label: string;
  hint?: string;
  icon?: IconName;
  shortcut?: string;
  group: "Actions" | "Navigation";
  run: () => void;
  disabled?: boolean;
};

type Entry = { kind: "command"; command: Command } | { kind: "hit"; hit: SearchHit };

export function CommandPalette({
  open,
  onClose,
  commands,
  onOpenHit,
}: {
  open: boolean;
  onClose: () => void;
  commands: Command[];
  onOpenHit: (hit: SearchHit) => void;
}) {
  const { onLost } = useAuth();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setHits([]);
      setCursor(0);
      window.setTimeout(() => input.current?.focus(), 10);
    }
  }, [open]);

  useEffect(() => {
    if (!open || query.trim().length < 2) {
      setHits([]);
      return;
    }
    const ac = new AbortController();
    const timer = window.setTimeout(() => {
      searchMails(query.trim(), ac.signal)
        .then((res) => setHits(res.hits.slice(0, 8)))
        .catch((err) => {
          if (err instanceof AuthError) onLost();
        });
    }, 120);
    return () => {
      window.clearTimeout(timer);
      ac.abort();
    };
  }, [query, open, onLost]);

  const entries = useMemo<Entry[]>(() => {
    const needle = query.trim().toLocaleLowerCase("fr");
    const filtered = commands.filter((command) => !command.disabled && (!needle || command.label.toLocaleLowerCase("fr").includes(needle) || (command.hint || "").toLocaleLowerCase("fr").includes(needle)));
    const out: Entry[] = filtered.map((command) => ({ kind: "command", command }));
    for (const hit of hits) out.push({ kind: "hit", hit });
    return out;
  }, [commands, hits, query]);

  useEffect(() => setCursor(0), [entries.length, query]);

  function run(entry: Entry) {
    onClose();
    if (entry.kind === "command") entry.command.run();
    else onOpenHit(entry.hit);
  }

  return (
    <Sheet open={open} title="Palette de commandes" onClose={onClose} wide>
      <div className="palette">
        <label className="palette-input">
          <Icon name="search" />
          <span className="sr-only">Commande ou recherche</span>
          <input
            ref={input}
            value={query}
            onChange={(ev) => setQuery(ev.target.value)}
            placeholder="Une action, une page, ou un mail…"
            autoComplete="off"
            spellCheck={false}
            onKeyDown={(ev) => {
              if (ev.key === "ArrowDown") {
                ev.preventDefault();
                setCursor((prev) => Math.min(entries.length - 1, prev + 1));
              } else if (ev.key === "ArrowUp") {
                ev.preventDefault();
                setCursor((prev) => Math.max(0, prev - 1));
              } else if (ev.key === "Enter" && entries[cursor]) {
                ev.preventDefault();
                run(entries[cursor]);
              }
            }}
          />
          <Kbd>esc</Kbd>
        </label>
        <ul className="palette-list" role="listbox">
          {entries.length === 0 ? <li className="palette-empty">Rien ne correspond.</li> : null}
          {entries.map((entry, index) => {
            const active = index === cursor;
            if (entry.kind === "command") {
              const command = entry.command;
              const first = index === 0 || entries[index - 1].kind !== "command" || (entries[index - 1] as { command: Command }).command.group !== command.group;
              return (
                <li key={command.id} role="option" aria-selected={active} className={`palette-row${active ? " is-on" : ""}`} onMouseEnter={() => setCursor(index)} onClick={() => run(entry)}>
                  {first ? <span className="palette-group">{command.group}</span> : null}
                  <span className="palette-main">
                    {command.icon ? <Icon name={command.icon} /> : <span className="icon" />}
                    <span className="palette-label">{command.label}</span>
                    {command.hint ? <span className="palette-hint">{command.hint}</span> : null}
                  </span>
                  {command.shortcut ? <Kbd>{command.shortcut}</Kbd> : null}
                </li>
              );
            }
            const hit = entry.hit;
            const first = index === 0 || entries[index - 1].kind !== "hit";
            return (
              <li key={`hit-${hit.id}`} role="option" aria-selected={active} className={`palette-row${active ? " is-on" : ""}`} onMouseEnter={() => setCursor(index)} onClick={() => run(entry)}>
                {first ? <span className="palette-group">Mails</span> : null}
                <span className="palette-main">
                  <Icon name="mail" />
                  <span className="palette-label">{hit.subject}</span>
                  <span className="palette-hint">{hit.sender_name}</span>
                </span>
                <span className="palette-when">{hit.created_rel.replace(/^.*\(|\)$/g, "")}</span>
              </li>
            );
          })}
        </ul>
      </div>
    </Sheet>
  );
}
