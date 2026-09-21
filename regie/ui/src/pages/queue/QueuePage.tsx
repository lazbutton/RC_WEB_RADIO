import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AuthError, adoptMail, openAttachment, searchMails, type ActionKind, type SearchHit } from "../../api";
import { useAuth } from "../../auth";
import { Button } from "../../components/Button";
import { SegmentedTabs, type Segment } from "../../components/SegmentedTabs";
import { useToasts } from "../../components/Toast";
import { flashKey, useKeyboard } from "../../lib/keyboard";
import { useStore } from "../../lib/store";
import { flattenSections, groupThreads, listSections, notionPieces, type Piece, type Thread } from "../../lib/threads";
import { ActionBar } from "./ActionBar";
import { DraftCard, NotionCard, PiecesCard, RecapCard, ThreadActions } from "./Cards";
import { HistoryList } from "./HistoryList";
import { HitReader } from "./HitReader";
import { Reader } from "./Reader";
import { SearchBox } from "./SearchBox";
import { SelectionBar } from "./SelectionBar";
import { ThreadList } from "./ThreadList";
import type { QuickAction } from "./ThreadRow";

const MQ = "(max-width: 800px)";

export function QueuePage({ view }: { view: "queue" | "history" }) {
  const store = useStore();
  const toasts = useToasts();
  const { onLost } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const cat = params.get("cat") || "";
  const wanted = params.get("id");
  const q = params.get("q") || "";
  const hid = params.get("hid") || "";
  const [needle, setNeedle] = useState(q);
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searchBusy, setSearchBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [notionNote, setNotionNote] = useState("");
  const [briefing, setBriefing] = useState(false);
  const [adopting, setAdopting] = useState(false);
  const [pieceBusy, setPieceBusy] = useState<string | null>(null);
  const [audio, setAudio] = useState<{ key: string; url: string } | null>(null);
  const [checked, setChecked] = useState<Set<string>>(() => new Set());
  const [showDetail, setShowDetail] = useState(() => !window.matchMedia(MQ).matches);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const briefed = useRef(new Set<string>());
  const searching = q.trim().length >= 2;
  const isHistory = view === "history";
  const payload = isHistory ? store.history : store.queue;
  const labels = payload?.labels ?? store.queue?.labels ?? {};
  const canMove = Boolean(store.queue?.can_move);

  useEffect(() => {
    if (isHistory) void store.refresh("history");
  }, [isHistory, store.refresh]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const nextQ = needle.trim().length >= 2 ? needle.trim() : "";
      if (nextQ === q) return;
      const next = new URLSearchParams(params);
      if (nextQ) next.set("q", nextQ);
      else next.delete("q");
      next.delete("hid");
      setParams(next, { replace: true });
    }, 120);
    return () => window.clearTimeout(timer);
  }, [needle]);

  useEffect(() => {
    if (!searching) {
      setHits(null);
      setSearchBusy(false);
      return;
    }
    const ac = new AbortController();
    setSearchBusy(true);
    searchMails(q, ac.signal)
      .then((res) => {
        setHits(res.hits);
        setSearchBusy(false);
      })
      .catch((err) => {
        if (err instanceof AuthError) onLost();
        else if (!(err instanceof DOMException && err.name === "AbortError")) {
          setSearchBusy(false);
          toasts.push({ text: err instanceof Error ? err.message : "recherche impossible", tone: "live" });
        }
      });
    return () => ac.abort();
  }, [q, searching, onLost, toasts]);

  const rows = useMemo(() => {
    if (!payload) return [];
    const out = [];
    for (const row of payload.items) {
      const fresh = store.items[row.id] ?? row;
      const keep = isHistory ? fresh.status === "moved" || fresh.status === "skipped" : fresh.status === "proposed" || store.leaving.has(row.id);
      if (keep && (!cat || fresh.category === cat)) out.push(fresh);
    }
    return out;
  }, [payload, store.items, store.leaving, cat, isHistory]);

  const threads = useMemo(() => groupThreads(rows), [rows]);
  const sections = useMemo(() => listSections(threads, !cat && !isHistory), [threads, cat, isHistory]);
  const ordered = useMemo(() => flattenSections(sections), [sections]);
  const selectedHit = useMemo(() => (searching && hits?.length ? hits.find((hit) => String(hit.id) === hid) ?? hits[0] : null), [hits, hid, searching]);
  const hitThread = useMemo(() => {
    if (!selectedHit) return null;
    const item = selectedHit.item_id ? store.items[selectedHit.item_id] ?? selectedHit.item : selectedHit.item;
    return item ? groupThreads([item])[0] ?? null : null;
  }, [selectedHit, store.items]);
  const queueSelected = useMemo(() => {
    if (!ordered.length) return null;
    return ordered.find((thread) => thread.ids.some((id) => String(id) === String(wanted ?? ""))) ?? ordered[0];
  }, [ordered, wanted]);
  const selected: Thread | null = searching ? hitThread : queueSelected;
  const draft = selected?.latest.draft || "";
  const briefJob = selected ? store.itemJob(selected.latest.id, "brief") : null;
  const notionJob = selected ? store.itemJob(selected.latest.id, "notion") : null;

  const select = useCallback(
    (thread: Thread) => {
      const next = new URLSearchParams(params);
      next.set("id", String(thread.latest.id));
      next.delete("hid");
      setParams(next, { replace: true });
      if (window.matchMedia(MQ).matches) setShowDetail(true);
    },
    [params, setParams],
  );

  const selectHit = useCallback(
    (hit: SearchHit) => {
      const next = new URLSearchParams(params);
      next.set("hid", String(hit.id));
      if (hit.item_id) next.set("id", String(hit.item_id));
      setParams(next, { replace: true });
      if (window.matchMedia(MQ).matches) setShowDetail(true);
    },
    [params, setParams],
  );

  function setCat(nextCat: string) {
    setNeedle("");
    setChecked(new Set());
    const next = new URLSearchParams();
    if (nextCat) next.set("cat", nextCat);
    setParams(next, { replace: true });
  }

  const doAction = useCallback(
    async (kind: ActionKind, ids: number[], opts: { advance?: boolean; silent?: boolean } = {}) => {
      if (!ids.length) return;
      const removes = kind === "archive" || kind === "later" || kind === "restore" || kind === "unlater";
      if (removes && opts.advance !== false && selected && ids.some((id) => selected.ids.includes(id))) {
        const idx = ordered.findIndex((thread) => thread.key === selected.key);
        const following = ordered.slice(idx + 1).find((thread) => !thread.ids.some((id) => ids.includes(id))) ?? ordered.slice(0, idx).reverse().find((thread) => !thread.ids.some((id) => ids.includes(id))) ?? null;
        const next = new URLSearchParams(params);
        if (following) next.set("id", String(following.latest.id));
        else next.delete("id");
        setParams(next, { replace: true });
      }
      setChecked((prev) => {
        if (!prev.size) return prev;
        const next = new Set(prev);
        for (const thread of ordered) if (thread.ids.some((id) => ids.includes(id))) next.delete(thread.key);
        return next;
      });
      await store.act(kind, ids, { silent: opts.silent });
      if (isHistory) void store.refresh("history");
    },
    [isHistory, ordered, params, selected, setParams, store],
  );

  const onQuick = useCallback((action: QuickAction) => void doAction(action.kind, action.ids), [doAction]);

  const onToggleCheck = useCallback((thread: Thread, ev: MouseEvent) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (ev.shiftKey && prev.size) {
        const keys = ordered.map((row) => row.key);
        const last = Array.from(prev).pop();
        const a = keys.indexOf(last || "");
        const b = keys.indexOf(thread.key);
        if (a >= 0 && b >= 0) {
          for (const key of keys.slice(Math.min(a, b), Math.max(a, b) + 1)) next.add(key);
          return next;
        }
      }
      if (next.has(thread.key)) next.delete(thread.key);
      else next.add(thread.key);
      return next;
    });
  }, [ordered]);

  const checkedIds = useMemo(() => ordered.filter((thread) => checked.has(thread.key)).flatMap((thread) => thread.ids), [ordered, checked]);

  async function onCopy() {
    if (!draft) return;
    try {
      await navigator.clipboard.writeText(draft);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toasts.push({ text: "Copie impossible dans ce navigateur", tone: "live" });
    }
  }

  async function onBrief() {
    if (!selected) return;
    setBriefing(true);
    try {
      await store.requestBrief(selected.latest.id);
    } finally {
      window.setTimeout(() => setBriefing(false), 800);
    }
  }

  async function onNotion() {
    if (!selected || selected.notionUrl) return;
    await store.requestNotion(selected.latest.id, notionNote);
    setNotionNote("");
  }

  async function onPiece(piece: Piece) {
    const key = `${piece.itemId}-${piece.n}`;
    if (piece.kind === "other") return;
    setPieceBusy(key);
    try {
      const blob = await openAttachment(piece.itemId, piece.n);
      const url = URL.createObjectURL(blob);
      if (piece.kind === "audio") {
        setAudio((prev) => {
          if (prev) URL.revokeObjectURL(prev.url);
          return { key, url };
        });
      } else {
        window.open(url, "_blank", "noopener");
        window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      }
    } catch (err) {
      if (err instanceof AuthError) onLost();
      else toasts.push({ text: err instanceof Error ? err.message : "pièce refusée", tone: "live" });
    } finally {
      setPieceBusy(null);
    }
  }

  async function onAdopt(queue: boolean) {
    if (!selectedHit) return;
    setAdopting(true);
    try {
      const item = await adoptMail(selectedHit.id, queue);
      if (item) {
        store.patchItem(item);
        setHits((prev) => prev?.map((hit) => (hit.id === selectedHit.id ? { ...hit, item, item_id: item.id, category: item.category, status: item.status } : hit)) ?? prev);
        if (!queue) void store.requestBrief(item.id);
        else toasts.push({ text: "Mail ajouté à la file À trier", tone: "ok", duration: 3000 });
      }
    } catch (err) {
      if (err instanceof AuthError) onLost();
      else toasts.push({ text: err instanceof Error ? err.message : "impossible", tone: "live" });
    } finally {
      setAdopting(false);
    }
  }

  useEffect(() => {
    setNotionNote("");
    setAudio((prev) => {
      if (prev) URL.revokeObjectURL(prev.url);
      return null;
    });
  }, [selected?.key]);

  useEffect(() => {
    if (!selected || isHistory || !store.queue?.llm_ready) return;
    const target = [...selected.items].reverse().find((row) => row.needs_brief);
    if (!target || briefed.current.has(selected.key) || store.itemJob(target.id, "brief")) return;
    briefed.current.add(selected.key);
    void store.requestBrief(target.id);
  }, [selected?.key, isHistory, store]);

  useEffect(() => {
    if (!selected || isHistory || !store.queue?.mark_read_on_open || !selected.unseen) return;
    const ids = selected.items.filter((item) => item.seen === false).map((item) => item.id);
    const timer = window.setTimeout(() => void store.act("seen", ids, { silent: true }), 1500);
    return () => window.clearTimeout(timer);
  }, [selected?.key, selected?.unseen, isHistory, store]);

  useEffect(() => {
    const el = document.querySelector<HTMLElement>(".thread-row.is-on");
    el?.scrollIntoView({ block: "nearest" });
  }, [selected?.key]);

  useEffect(() => {
    if (showDetail && window.matchMedia(MQ).matches) {
      window.setTimeout(() => document.querySelector<HTMLElement>(".reader-title")?.focus(), 30);
    }
  }, [showDetail, selected?.key]);

  const move = useCallback(
    (delta: number) => {
      if (searching) {
        if (!hits?.length || !selectedHit) return;
        const idx = hits.findIndex((hit) => hit.id === selectedHit.id);
        const next = hits[idx + delta];
        if (next) selectHit(next);
        return;
      }
      if (!selected) return;
      const idx = ordered.findIndex((thread) => thread.key === selected.key);
      const next = ordered[idx + delta];
      if (next) select(next);
    },
    [hits, ordered, searching, select, selectHit, selected, selectedHit],
  );

  useKeyboard({
    "/": () => searchRef.current?.focus(),
    Escape: () => {
      if (document.activeElement === searchRef.current && needle) {
        setNeedle("");
        return true;
      }
      if (checked.size) {
        setChecked(new Set());
        return true;
      }
      if (needle) {
        setNeedle("");
        return true;
      }
      return false;
    },
    j: () => move(1),
    k: () => move(-1),
    ArrowDown: () => move(1),
    ArrowUp: () => move(-1),
    e: () => (selected && !isHistory && canMove ? void doAction("archive", selected.ids) : false),
    "shift+e": () => (checkedIds.length && canMove ? void doAction("archive", checkedIds) : false),
    u: () => (selected && !isHistory ? void doAction(selected.unseen ? "seen" : "unseen", selected.ids) : false),
    s: () => (selected && !isHistory ? void doAction(selected.flagged ? "unflag" : "flag", selected.ids) : false),
    l: () => (selected && !isHistory ? void doAction("later", selected.ids) : false),
    r: () => (selected && isHistory ? void doAction(selected.latest.status === "moved" ? "restore" : "unlater", selected.ids) : false),
    c: () => (draft ? void onCopy() : false),
    n: () => (selected && !selected.notionUrl ? void onNotion() : false),
    x: () => {
      if (!selected) return false;
      setChecked((prev) => {
        const next = new Set(prev);
        if (next.has(selected.key)) next.delete(selected.key);
        else next.add(selected.key);
        return next;
      });
      return true;
    },
    z: () => {
      void store.undoLast();
    },
    g: () => {
      navigate(isHistory ? "/" : "/history");
    },
  });

  const catSegments: Segment[] = useMemo(() => {
    const counts = store.queue?.cat_counts ?? {};
    const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
    const out: Segment[] = [{ id: "", label: "Tous", count: total }];
    for (const key of store.queue?.categories ?? []) {
      const n = counts[key] || 0;
      if (!n && cat !== key) continue;
      out.push({ id: key, label: labels[key] || key, count: n, tone: key });
    }
    return out;
  }, [store.queue?.cat_counts, store.queue?.categories, labels, cat]);

  const indexMeta = useMemo(() => {
    if (searching) return hits ? `${hits.length} résultat${hits.length > 1 ? "s" : ""}` : "";
    const indexed = store.index.indexed || 0;
    const inbox = store.index.inbox || 0;
    if (inbox && indexed < inbox) return `${indexed} / ${inbox}`;
    return indexed ? `${indexed} mails` : "";
  }, [searching, hits, store.index]);

  const tools = (
    <>
      {!isHistory ? <SegmentedTabs segments={catSegments} value={searching ? "__search" : cat} onChange={setCat} ariaLabel="Catégories" className="cats" /> : <div className="cats is-history-title">Mails traités</div>}
      <SearchBox ref={searchRef} value={needle} onChange={setNeedle} meta={indexMeta} busy={searchBusy} />
      {checked.size ? (
        <SelectionBar
          count={checked.size}
          canMove={canMove}
          onAction={(kind) => void doAction(kind, checkedIds, { advance: false })}
          onClear={() => setChecked(new Set())}
          onAll={() => setChecked(new Set(ordered.map((thread) => thread.key)))}
        />
      ) : null}
    </>
  );

  return (
    <div className={`desk${showDetail ? " show-detail" : ""}`} data-density={store.queue?.density || "comfortable"}>
      <ThreadList
        view={view}
        loading={store.loading && !payload}
        sections={sections}
        hits={hits}
        searching={searching}
        searchBusy={searchBusy}
        q={q}
        cat={cat}
        selectedKey={selected?.key ?? null}
        selectedHitId={selectedHit?.id ?? null}
        leaving={store.leaving}
        checked={checked}
        labels={labels}
        canMove={canMove}
        newCount={store.newIds.length}
        scanRunning={store.scanRunning}
        treatedToday={store.queue?.treated_today || 0}
        tools={tools}
        scrollRef={listRef}
        onSelect={select}
        onSelectHit={selectHit}
        onToggleCheck={onToggleCheck}
        onQuick={onQuick}
        onShowNew={() => {
          store.clearNew();
          listRef.current?.scrollTo({ top: 0, behavior: "smooth" });
        }}
      />
      <section className="inspector" aria-live="polite">
        {selected ? (
          <>
            <div className="detail">
              <Reader
                key={selected.key}
                thread={selected}
                labels={labels}
                history={isHistory}
                toolbar={
                  <ActionBar
                    thread={selected}
                    canMove={canMove}
                    history={isHistory}
                    onAction={(kind) => void doAction(kind, selected.ids)}
                    onNotion={() => void onNotion()}
                    onCopy={() => void onCopy()}
                    hasDraft={Boolean(draft)}
                    copied={copied}
                    notionBusy={Boolean(notionJob)}
                    notionUrl={selected.notionUrl}
                  />
                }
              />
              <aside className="rail">
                <RecapCard thread={selected} job={briefJob} briefing={briefing} onBrief={() => void onBrief()} history={isHistory} />
                {draft ? <DraftCard draft={draft} copied={copied} onCopy={() => void onCopy()} /> : null}
                <PiecesCard pieces={selected.pieces} busyKey={pieceBusy} audio={audio} onOpen={(piece) => void onPiece(piece)} />
                <NotionCard thread={selected} ready={Boolean(store.queue?.notion_ready)} job={notionJob} note={notionNote} onNote={setNotionNote} onAdd={() => void onNotion()} />
                <ThreadActions actions={store.actions} ids={selected.ids} onUndo={(id) => void store.undo(id)} />
                {isHistory ? <HistoryList actions={store.actions} onUndo={(id) => void store.undo(id)} /> : null}
                {notionPieces(selected.pieces).length === 0 && selected.pieces.length ? null : null}
              </aside>
            </div>
            <div className="inspector-foot">
              <Button variant="quiet" icon="arrow-left" className="back-list" onClick={() => setShowDetail(false)}>
                Liste
              </Button>
              <a className="foot-link" href="https://webmail.radiocampus.org" target="_blank" rel="noreferrer">
                Webmail
              </a>
              <span className="foot-keys" aria-hidden>
                <button
                  type="button"
                  className="foot-help"
                  onClick={() => {
                    flashKey("?");
                    window.dispatchEvent(new CustomEvent("inboxzero-help"));
                  }}
                  data-key="?"
                >
                  ? raccourcis
                </button>
              </span>
            </div>
          </>
        ) : searching && selectedHit ? (
          <HitReader hit={selectedHit} q={q} labels={labels} onAdopt={(queue) => void onAdopt(queue)} busy={adopting} />
        ) : isHistory ? (
          <div className="detail is-solo">
            <aside className="rail is-wide">
              <HistoryList actions={store.actions} onUndo={(id) => void store.undo(id)} />
            </aside>
          </div>
        ) : (
          <div className="inspector-empty" aria-hidden />
        )}
      </section>
    </div>
  );
}
