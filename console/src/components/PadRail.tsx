import { Badge, IconButton, Switch, Text, Tooltip } from "@radix-ui/themes";
import { DoubleArrowLeftIcon, DoubleArrowRightIcon } from "@radix-ui/react-icons";
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { formatMmSs } from "../lib/parisClock";
import { type CatalogItem } from "../mock/catalog";
import { padItems, useStation } from "../mock/StationContext";

const PADS_KEY = "app-pads-collapsed";

function readPadsCollapsed(): boolean {
  try {
    const stored = window.localStorage.getItem(PADS_KEY);
    if (stored === "1") return true;
    if (stored === "0") return false;
  } catch {
    /* proto */
  }
  return false;
}

export function PadRail() {
  const { insertNow } = useStation();
  const sons = padItems("son");
  const jingles = padItems("jingle");
  const pubs = padItems("pub");

  const [collapsed, setCollapsed] = useState(readPadsCollapsed);
  const [chainAfterSound, setChainAfterSound] = useState(true);
  const [jingleArmed, setJingleArmed] = useState(false);
  const [firedId, setFiredId] = useState<string | null>(null);
  const [focusBank, setFocusBank] = useState<"son" | "jingle" | "pub" | null>(null);
  const firstJingleRef = useRef<HTMLButtonElement>(null);
  const firstSonRef = useRef<HTMLButtonElement>(null);
  const firstPubRef = useRef<HTMLButtonElement>(null);
  const chainRef = useRef(chainAfterSound);
  chainRef.current = chainAfterSound;

  const pulse = (id: string) => {
    setFiredId(id);
    window.setTimeout(() => setFiredId(null), 700);
  };

  const fireJingle = useCallback(
    (item: CatalogItem) => {
      pulse(item.id);
      insertNow(item);
      setJingleArmed(false);
    },
    [insertNow],
  );

  const fireSon = useCallback(
    (item: CatalogItem) => {
      pulse(item.id);
      insertNow(item);
      if (chainRef.current) {
        setCollapsed(false);
        try {
          window.localStorage.setItem(PADS_KEY, "0");
        } catch {
          /* proto */
        }
        setJingleArmed(true);
        window.setTimeout(() => firstJingleRef.current?.focus(), 0);
      }
    },
    [insertNow],
  );

  const firePub = useCallback(
    (item: CatalogItem) => {
      pulse(item.id);
      insertNow(item);
    },
    [insertNow],
  );

  const toggle = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(PADS_KEY, next ? "1" : "0");
      } catch {
        /* proto */
      }
      return next;
    });
  }, []);

  const openBank = useCallback((kind: "son" | "jingle" | "pub") => {
    setCollapsed(false);
    try {
      window.localStorage.setItem(PADS_KEY, "0");
    } catch {
      /* proto */
    }
    setFocusBank(kind);
  }, []);

  useEffect(() => {
    if (!focusBank) return;
    const ref =
      focusBank === "jingle" ? firstJingleRef : focusBank === "son" ? firstSonRef : firstPubRef;
    window.setTimeout(() => ref.current?.focus(), 0);
    setFocusBank(null);
  }, [focusBank, collapsed]);

  return (
    <aside className={`desk-pad-rail${collapsed ? " is-collapsed" : ""}`} aria-label="Pads">
      <div className="desk-pad-rail-head">
        {collapsed ? null : (
          <Text size="3" weight="bold">
            Pads
          </Text>
        )}
        <Tooltip content={collapsed ? "Déplier les pads" : "Replier les pads"}>
          <IconButton
            variant="ghost"
            color="gray"
            size="1"
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Déplier les pads" : "Replier les pads"}
            onClick={toggle}
          >
            {collapsed ? <DoubleArrowLeftIcon /> : <DoubleArrowRightIcon />}
          </IconButton>
        </Tooltip>
      </div>

      {collapsed ? (
        <div className="desk-pad-rail-icons">
          <Tooltip content="Sons" side="left">
            <button
              type="button"
              className="pad-rail-icon is-son"
              aria-label="Ouvrir les sons"
              onClick={() => openBank("son")}
            >
              S
            </button>
          </Tooltip>
          <Tooltip content={jingleArmed ? "Jingles — armé" : "Jingles"} side="left">
            <button
              type="button"
              className={`pad-rail-icon is-jingle${jingleArmed ? " is-armed" : ""}`}
              aria-label="Ouvrir les jingles"
              onClick={() => openBank("jingle")}
            >
              J
            </button>
          </Tooltip>
          <Tooltip content="Pubs" side="left">
            <button
              type="button"
              className="pad-rail-icon is-pub"
              aria-label="Ouvrir les pubs"
              onClick={() => openBank("pub")}
            >
              P
            </button>
          </Tooltip>
        </div>
      ) : (
        <div className="desk-pad-rail-body">
          <label className={`pad-rail-chain${chainAfterSound ? " is-on" : ""}`}>
            <Switch
              size="1"
              checked={chainAfterSound}
              onCheckedChange={(checked) => setChainAfterSound(checked === true)}
            />
            <span>
              <span className="pad-rail-chain-title">Jingle après le son</span>
              <span className="pad-rail-chain-hint">arme la banque Jingles</span>
            </span>
          </label>
          <PadBank
            title="Sons"
            kind="son"
            items={sons}
            firedId={firedId}
            onFire={fireSon}
            firstRef={firstSonRef}
          />
          <PadBank
            title="Jingles"
            kind="jingle"
            items={jingles}
            firedId={firedId}
            onFire={fireJingle}
            armed={jingleArmed}
            firstRef={firstJingleRef}
          />
          <PadBank
            title="Pubs"
            kind="pub"
            items={pubs}
            firedId={firedId}
            onFire={firePub}
            firstRef={firstPubRef}
          />
        </div>
      )}
    </aside>
  );
}

function PadBank({
  title,
  kind,
  items,
  firedId,
  onFire,
  armed,
  firstRef,
}: {
  title: string;
  kind: "son" | "jingle" | "pub";
  items: CatalogItem[];
  firedId: string | null;
  onFire: (item: CatalogItem) => void;
  armed?: boolean;
  firstRef?: RefObject<HTMLButtonElement | null>;
}) {
  return (
    <section className={`pad-bank is-${kind}${armed ? " is-armed" : ""}`}>
      <div className="pad-bank-head">
        <span className={`kind-dot is-${kind}`} aria-hidden="true" />
        <Text size="2" weight="bold">
          {title}
        </Text>
        {armed ? (
          <Badge size="1" color="ruby" variant="solid">
            Armé
          </Badge>
        ) : null}
        <span className="pad-bank-count">{items.length}</span>
      </div>
      <div className="pad-rail-list" aria-label={`Pads ${title}`}>
        {items.map((item, i) => (
          <button
            key={item.id}
            type="button"
            ref={i === 0 ? firstRef : undefined}
            className={`pad-btn pad-btn--${kind}${firedId === item.id ? " is-fired" : ""}${
              armed && i === 0 ? " is-next-fire" : ""
            }`}
            aria-label={`${title} ${i + 1} ${item.title}`}
            onClick={() => onFire(item)}
          >
            <span className="pad-btn-n">{i + 1}</span>
            <span className="pad-btn-title">{item.title}</span>
            <span className="pad-btn-dur">{formatMmSs(item.durationSec)}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
