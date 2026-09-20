import { useCallback, useEffect, useRef, useState } from "react";
import { HARBOR_MIN_MS, TAP_CORR_S, TAP_PRE_S, clockLabel } from "./format";
import { loadListenerHistory, pushListener } from "./history";
import { type IcecastSource, type IcecastStats, type NowPlaying, icecastSource } from "./icecast";
import { decodeTap, delayBehind, tapStream } from "./tap";
import type { HubUrls } from "./urls";

export type Ping = boolean | null;

export type MachineSnap = {
  status?: string;
  silence_s?: number;
  rms_db?: number;
  rms?: number;
  gain?: number;
  on?: boolean;
  harbor_min?: number;
  harbor_max?: number;
  harbor_s?: number | null;
  source?: string;
  rss_mb?: number;
  cpu_pct?: number;
  disk_pct?: number | null;
  disk_path?: string;
};

type InboxHealth = {
  ok?: boolean;
  pending?: number;
};

export type HubSnap = {
  ready: boolean;
  measuring: boolean;
  now: NowPlaying | null;
  nasgul: IcecastSource | null;
  vps: IcecastSource | null;
  publicOn: boolean;
  catalogOk: Ping;
  inboxOk: Ping;
  inboxPending: number | null;
  consoleOk: Ping;
  nowOk: Ping;
  studioMs: number | null;
  prerollMs: number | null;
  vpsDelay: number | null;
  vpsNote: string;
  lagNote: string;
  listeners: number[];
  machine: MachineSnap | null;
};

async function readJson<T>(url: string): Promise<T | null> {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

async function probe(url: string): Promise<boolean | null> {
  try {
    const response = await fetch(url, { cache: "no-store" });
    return response.ok;
  } catch {
    return null;
  }
}

const empty: HubSnap = {
  ready: false,
  measuring: false,
  now: null,
  nasgul: null,
  vps: null,
  publicOn: false,
  catalogOk: null,
  inboxOk: null,
  inboxPending: null,
  consoleOk: null,
  nowOk: null,
  studioMs: null,
  prerollMs: null,
  vpsDelay: null,
  vpsNote: "",
  lagNote: "",
  listeners: [],
  machine: null,
};

type Status = {
  now: NowPlaying | null;
  nasgul: IcecastSource | null;
  publicOn: boolean;
};

export function useHub(urls: HubUrls | null) {
  const [snap, setSnap] = useState<HubSnap>(() => ({ ...empty, listeners: loadListenerHistory() }));
  const measuring = useRef(false);

  const refresh = useCallback(async (): Promise<Status | null> => {
    if (!urls) return null;
    const [now, nasgulJson, publicJson, catalogOk, inbox, consoleOk, nowOk, machine] = await Promise.all([
      readJson<NowPlaying>(urls.now),
      readJson<IcecastStats>(urls.nasgulStatus),
      readJson<IcecastStats>(urls.publicStatus),
      probe("/probe/catalog"),
      readJson<InboxHealth>("/probe/inbox"),
      probe("/probe/console"),
      probe("/probe/now"),
      readJson<MachineSnap>("/probe/machine"),
    ]);
    const nasgul = icecastSource(nasgulJson);
    const vps = icecastSource(publicJson);
    const publicOn = Boolean(vps);
    setSnap((prev) => {
      const listeners = nasgul?.listeners;
      const history =
        typeof listeners === "number" ? pushListener(prev.listeners, listeners) : prev.listeners;
      const pending = inbox?.pending;
      return {
        ...prev,
        ready: true,
        now,
        nasgul,
        vps,
        publicOn,
        catalogOk,
        inboxOk: inbox != null && inbox.ok !== false,
        inboxPending: typeof pending === "number" && Number.isFinite(pending) ? pending : prev.inboxPending,
        consoleOk,
        nowOk,
        machine: machine ?? prev.machine,
        listeners: history,
      };
    });
    return { now, nasgul, publicOn };
  }, [urls]);

  const refreshNow = useCallback(async () => {
    if (!urls) return;
    const [now, machine] = await Promise.all([
      readJson<NowPlaying>(urls.now),
      readJson<MachineSnap>("/probe/machine"),
    ]);
    setSnap((prev) => ({
      ...prev,
      ready: true,
      now: now ?? prev.now,
      nowOk: now != null ? true : prev.nowOk,
      machine: machine ?? prev.machine,
    }));
  }, [urls]);

  const measure = useCallback(async () => {
    if (!urls || measuring.current) return;
    measuring.current = true;
    setSnap((prev) => ({ ...prev, measuring: true }));
    try {
      const current = await refresh();
      if (!current) return;
      const needCorr = Boolean(current.nasgul && current.publicOn && urls.streamNasgul && urls.streamPublic);
      if (!current.publicOn) {
        setSnap((prev) => ({ ...prev, vpsDelay: null, vpsNote: "sans source" }));
      }

      const paintPreroll = (kind: "studio" | "mp3", ms: number) => {
        setSnap((prev) => ({
          ...prev,
          studioMs: kind === "studio" ? ms : prev.studioMs,
          prerollMs: kind === "mp3" ? ms : prev.prerollMs,
          lagNote: prev.lagNote || `preroll à ${clockLabel()}`,
        }));
      };

      const [nasTap, studioTap, vpsTap] = await Promise.all([
        current.nasgul && urls.streamNasgul
          ? tapStream(urls.streamNasgul, needCorr ? TAP_CORR_S : TAP_PRE_S, {
              onPreroll: (ms) => paintPreroll("mp3", ms),
              stopOnPreroll: !needCorr,
            })
          : Promise.resolve({ ok: false as const }),
        urls.streamStudio
          ? tapStream(urls.streamStudio, TAP_PRE_S, {
              onPreroll: (ms) => paintPreroll("studio", ms),
              stopOnPreroll: true,
            })
          : Promise.resolve({ ok: false as const }),
        needCorr ? tapStream(urls.streamPublic, TAP_CORR_S) : Promise.resolve({ ok: false as const }),
      ]);

      let vpsDelay: number | null = null;
      let vpsNote = current.publicOn ? "" : "sans source";
      if (needCorr && nasTap.ok && vpsTap.ok) {
        const decoded = await Promise.all([decodeTap(nasTap), decodeTap(vpsTap)]);
        const delay = decoded[0] && decoded[1] ? delayBehind(decoded[0], decoded[1]) : null;
        if (delay) vpsDelay = delay.ms;
        else vpsNote = "pas de calage";
      }

      setSnap((prev) => ({
        ...prev,
        measuring: false,
        studioMs: studioTap.ok && studioTap.prerollMs != null ? studioTap.prerollMs : prev.studioMs,
        prerollMs: nasTap.ok && nasTap.prerollMs != null ? nasTap.prerollMs : prev.prerollMs,
        vpsDelay,
        vpsNote,
        lagNote: `mesuré à ${clockLabel()}`,
      }));
    } catch (err) {
      setSnap((prev) => ({
        ...prev,
        measuring: false,
        vpsNote: err instanceof Error ? err.message : "échec",
        lagNote: "mesure en échec",
      }));
    } finally {
      measuring.current = false;
    }
  }, [refresh, urls]);

  useEffect(() => {
    if (!urls) return;
    void refresh();
    void measure();
    const nowTick = window.setInterval(() => void refreshNow(), 2000);
    const status = window.setInterval(() => void refresh(), 8000);
    const lags = window.setInterval(() => void measure(), 30000);
    return () => {
      window.clearInterval(nowTick);
      window.clearInterval(status);
      window.clearInterval(lags);
    };
  }, [measure, refresh, refreshNow, urls]);

  return {
    snap,
    refresh,
    measure,
    harborMs: snap.machine?.harbor_min != null ? snap.machine.harbor_min * 1000 : HARBOR_MIN_MS,
  };
}
