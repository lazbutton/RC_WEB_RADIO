import { useCallback, useRef, useState } from "react";
import { fmtMs, isWavUrl } from "./format";
import { playStudioWav } from "./wav";

type StudioState = {
  on: boolean;
  starting: boolean;
  lagMs: number | null;
  error: string;
  analyser: AnalyserNode | null;
};

export function useStudio(getUrl: () => string) {
  const [state, setState] = useState<StudioState>({
    on: false,
    starting: false,
    lagMs: null,
    error: "",
    analyser: null,
  });
  const abort = useRef<AbortController | null>(null);
  const stopFn = useRef<(() => void) | null>(null);
  const duckFn = useRef<((gain: number, seconds: number) => void) | null>(null);
  const onRef = useRef(false);

  const stop = useCallback(() => {
    onRef.current = false;
    abort.current?.abort();
    abort.current = null;
    stopFn.current?.();
    stopFn.current = null;
    duckFn.current = null;
    setState({ on: false, starting: false, lagMs: null, error: "", analyser: null });
  }, []);

  const toggle = useCallback(async () => {
    if (onRef.current || state.starting) {
      stop();
      return;
    }
    const url = getUrl();
    if (!url) {
      setState((prev) => ({ ...prev, error: "pas de flux" }));
      return;
    }
    if (!isWavUrl(url)) {
      setState((prev) => ({ ...prev, error: "WAV requis" }));
      return;
    }
    onRef.current = true;
    const ctrl = new AbortController();
    abort.current = ctrl;
    setState({ on: true, starting: true, lagMs: null, error: "", analyser: null });
    try {
      const handle = await playStudioWav(
        url,
        ctrl.signal,
        (ms) => {
          if (onRef.current) setState((prev) => ({ ...prev, lagMs: ms, starting: false }));
        },
        (message) => {
          if (onRef.current) setState((prev) => ({ ...prev, error: message }));
        },
      );
      stopFn.current = handle.stop;
      duckFn.current = handle.duck;
      if (!onRef.current) {
        handle.stop();
        return;
      }
      setState((prev) => ({ ...prev, starting: false, analyser: handle.analyser }));
    } catch (err) {
      onRef.current = false;
      if (ctrl.signal.aborted) return;
      setState({
        on: false,
        starting: false,
        lagMs: null,
        error: err instanceof Error ? err.message : "échec",
        analyser: null,
      });
    }
  }, [getUrl, state.starting, stop]);

  const duck = useCallback((gain: number, seconds: number) => {
    duckFn.current?.(gain, seconds);
  }, []);

  return { ...state, toggle, duck, lagLabel: state.lagMs != null ? fmtMs(state.lagMs) : "" };
}
