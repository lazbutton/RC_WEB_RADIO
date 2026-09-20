import { HARBOR_MAX_S, HARBOR_MIN_S, META_DEAD_S, ageSeconds, fmtMs, fmtSec, sinceLabel } from "./lib/format";
import { sourceCodec, sourceMount } from "./lib/icecast";
import type { HubSnap, Ping } from "./lib/poll";

type Kind = "ok" | "bad" | "cfg" | "wait";

type Props = {
  snap: HubSnap;
  live: boolean;
  announce: boolean;
  harborMs: number;
  casqueLabel: string;
  casqueMs: number | null;
  onMeasure: () => void;
};

function Row({
  label,
  value,
  kind,
  loading,
  badge,
}: {
  label: string;
  value: string;
  kind: Kind;
  loading: boolean;
  badge?: string;
}) {
  return (
    <div className={`path-row is-${kind}`}>
      <span>{label}</span>
      {loading ? (
        <b className="skel skel-ms" />
      ) : (
        <b>
          {value}
          {badge ? <i className="path-duck">{badge}</i> : null}
        </b>
      )}
    </div>
  );
}

function Dot({ label, ping, ready }: { label: string; ping: Ping | boolean; ready: boolean }) {
  const kind = !ready ? "wait" : ping ? "ok" : "bad";
  return (
    <li className={`puce is-${kind}`}>
      <i aria-hidden="true" />
      {label}
    </li>
  );
}

function num(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function Path({ snap, live, announce, harborMs, casqueLabel, casqueMs, onMeasure }: Props) {
  const waiting = !snap.ready;
  const prerollPending = snap.measuring && snap.studioMs == null && snap.prerollMs == null;
  let vpsValue = "";
  let vpsKind: Kind = "wait";
  if (!waiting && !snap.publicOn) {
    vpsValue = snap.vpsNote || "sans source";
    vpsKind = "bad";
  } else if (snap.vpsDelay != null) {
    vpsValue = fmtMs(snap.vpsDelay);
    vpsKind = snap.vpsDelay > 250 ? "bad" : "ok";
  } else if (snap.vpsNote) {
    vpsValue = snap.vpsNote;
    vpsKind = "bad";
  }

  const casqueKind: Kind =
    casqueMs != null ? (casqueMs > 2000 ? "bad" : "ok") : prerollPending || waiting ? "wait" : "bad";
  const mp3Kind: Kind =
    snap.prerollMs != null ? (snap.prerollMs > 500 ? "bad" : "ok") : prerollPending || waiting ? "wait" : "bad";
  const vpsLoading = waiting || (snap.measuring && snap.publicOn && !vpsValue);
  const calageVps = snap.measuring && (snap.studioMs != null || snap.prerollMs != null);

  const machine = snap.machine;
  const hasMachine = machine != null;
  const gain = num(machine?.gain);
  const rms = num(machine?.rms);
  const rawSilence = num(machine?.silence_s) ?? 0;
  const meterOk = rms != null && rms > 0.0005;
  const duck = Boolean(machine?.on);
  const harborMin = num(machine?.harbor_min) ?? (harborMs / 1000 || HARBOR_MIN_S);
  const harborMax = num(machine?.harbor_max) ?? HARBOR_MAX_S;
  const harborBuf = num(machine?.harbor_s ?? undefined);
  const liveHarbor = (machine?.source || snap.now?.source || "") === "stream";
  const harborText =
    harborBuf != null
      ? `${harborMin}–${harborMax} s · ${fmtSec(harborBuf)}`
      : `${harborMin}–${harborMax} s · ${liveHarbor ? "live" : "auto"}`;

  const nasgulN = num(snap.nasgul?.listeners);
  const vpsN = num(snap.vps?.listeners);
  const metaAge = ageSeconds(snap.now?.received_at);
  const metaDead = metaAge == null || metaAge > META_DEAD_S;
  const silence = meterOk ? rawSilence : snap.nasgul && !metaDead ? 0 : rawSilence;
  const mount = sourceMount(snap.nasgul) || "button.mp3";
  const codec = sourceCodec(snap.nasgul) || "MP3";
  const cpu = num(machine?.cpu_pct);
  const ram = num(machine?.rss_mb);
  const ramOk = ram != null && ram >= 8;
  const cpuOk = cpu != null && cpu > 0;
  const playoutText =
    !cpuOk && !ramOk
      ? "—"
      : `${cpuOk ? Math.round(cpu as number) : "—"} % · ${ramOk ? Math.round(ram as number) : "—"} Mo`;
  const disk = num(machine?.disk_pct);
  const iceSince = sinceLabel(snap.nasgul?.stream_start_iso8601 || snap.nasgul?.stream_start);
  const pending = snap.inboxPending;

  return (
    <aside className="path">
      {snap.ready ? (
        <p className={`badge${live || announce ? " is-live" : ""}`}>
          {live || announce ? <span className="live-dot" aria-hidden="true" /> : null}
          {announce ? "Annonce" : live ? "Live" : "Antenne"}
        </p>
      ) : (
        <p className="skel skel-badge" />
      )}
      <Row
        label="Gain"
        value={gain != null ? `${Math.round(gain * 100)} %` : "—"}
        kind={duck ? "ok" : !hasMachine ? (waiting ? "wait" : "cfg") : "cfg"}
        loading={waiting && !hasMachine}
        badge={duck ? "duck" : undefined}
      />
      <Row
        label="Silence"
        value={hasMachine ? fmtSec(silence) : "—"}
        kind={!hasMachine ? (waiting ? "wait" : "bad") : silence > 0 ? "bad" : "ok"}
        loading={waiting && !hasMachine}
      />

      <p className="path-kicker">Chemin d’écoute</p>
      <Row
        label="Casque"
        value={casqueLabel || "hors ligne"}
        kind={casqueKind}
        loading={waiting || (prerollPending && !casqueLabel)}
      />
      <Row
        label="MP3 Nasgul"
        value={snap.prerollMs != null ? fmtMs(snap.prerollMs) : "hors ligne"}
        kind={mp3Kind}
        loading={waiting || (prerollPending && snap.prerollMs == null)}
      />
      <Row label="Harbor" value={harborText} kind={liveHarbor ? "ok" : "cfg"} loading={false} />
      <Row label="Nasgul → VPS" value={vpsValue || "calage"} kind={vpsKind} loading={vpsLoading} />
      <button type="button" className="path-remeasure" onClick={onMeasure} disabled={snap.measuring}>
        {snap.measuring ? (calageVps ? "Calage VPS" : "Mesure") : "Relancer le calage"}
      </button>
      {snap.lagNote ? <p className="path-note">{snap.lagNote}</p> : <p className="skel skel-note" />}

      <p className="path-kicker">Flux</p>
      <Row
        label="Auditeurs"
        value={`VPS ${vpsN ?? "—"} · Nasgul ${nasgulN ?? "—"}`}
        kind={waiting ? "wait" : snap.publicOn ? "ok" : "bad"}
        loading={waiting}
      />
      <Row
        label="Métas"
        value={metaAge == null ? "mort" : fmtSec(metaAge)}
        kind={waiting ? "wait" : metaDead ? "bad" : "ok"}
        loading={waiting}
      />
      <Row label="Codec" value={`${codec} · ${mount}`} kind={waiting ? "wait" : "cfg"} loading={waiting} />
      <Row label="Studio" value="WAV" kind="cfg" loading={false} />

      <p className="path-kicker">Banque</p>
      <Row
        label="Inbox"
        value={pending == null ? "—" : `${pending} à classer`}
        kind={waiting ? "wait" : !snap.inboxOk ? "bad" : pending && pending > 0 ? "cfg" : "ok"}
        loading={waiting && pending == null}
      />

      <p className="path-kicker">Machine</p>
      <Row
        label="Playout"
        value={playoutText}
        kind={!hasMachine ? (waiting ? "wait" : "cfg") : "cfg"}
        loading={waiting && !hasMachine}
      />
      <Row
        label="Média"
        value={
          disk == null
            ? "—"
            : disk > 0 && disk < 1
              ? `${disk.toFixed(1).replace(".", ",")} %`
              : `${Math.round(disk)} %`
        }
        kind={!hasMachine ? (waiting ? "wait" : "bad") : disk != null && disk >= 90 ? "bad" : "cfg"}
        loading={waiting && !hasMachine}
      />
      <Row
        label="Icecast"
        value={iceSince || "—"}
        kind={waiting ? "wait" : iceSince ? "ok" : "bad"}
        loading={waiting}
      />

      <p className="path-kicker">Santé</p>
      <ul className="puces">
        <Dot label="Console" ping={snap.consoleOk} ready={snap.ready} />
        <Dot label="Icecast" ping={Boolean(snap.nasgul)} ready={snap.ready} />
        <Dot label="VPS" ping={snap.publicOn} ready={snap.ready} />
        <Dot label="Catalogue" ping={snap.catalogOk} ready={snap.ready} />
        <Dot label="Inbox" ping={snap.inboxOk} ready={snap.ready} />
        <Dot label="Afficheur" ping={snap.nowOk && !metaDead} ready={snap.ready} />
      </ul>
    </aside>
  );
}
