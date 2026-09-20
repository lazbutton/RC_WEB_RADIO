import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CommandK } from "./CommandK";
import { Path } from "./Path";
import { Rail } from "./Rail";
import { Scene } from "./Scene";
import { loadBrand } from "./lib/brand";
import { fmtMs, sinceLabel, sourceLabel } from "./lib/format";
import { useHub } from "./lib/poll";
import { useStudio } from "./lib/studio";
import { useVoiceover } from "./lib/voiceover";
import { ateliers, urlsFromBrand, type HubUrls } from "./lib/urls";
import { useClock } from "./lib/useClock";
import type { Brand } from "./vite-env";

export function App() {
  const [brand, setBrand] = useState<Brand | null>(null);
  const [dock, setDock] = useState(false);
  const clock = useClock();
  const urls = useMemo(() => (brand ? urlsFromBrand(brand) : null), [brand]);
  const urlsRef = useRef<HubUrls | null>(null);
  urlsRef.current = urls;
  const { snap, measure, harborMs } = useHub(urls);
  const getStudioUrl = useCallback(() => urlsRef.current?.streamStudio || "", []);
  const studio = useStudio(getStudioUrl);
  const voiceover = useVoiceover(studio.duck);
  const list = urls ? ateliers(urls) : [];
  const live = /live|stream|harbor/i.test(snap.now?.source || "");
  const since = sinceLabel(snap.now?.on_air || snap.nasgul?.stream_start_iso8601 || snap.nasgul?.stream_start);
  const casqueMs = studio.on && studio.lagMs != null ? studio.lagMs : snap.studioMs;
  const casqueLabel = casqueMs != null ? fmtMs(casqueMs) : "";

  const openDock = useCallback(() => setDock(true), []);
  const closeDock = useCallback(() => setDock(false), []);

  useEffect(() => {
    void loadBrand()
      .then((next) => {
        setBrand(next);
        document.title = next.suite?.name || "BUTTON";
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setDock((open) => !open);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const name = brand?.suite.name || "BUTTON";

  return (
    <div className="shell">
      <Rail
        name={name}
        clock={clock}
        source={sourceLabel(snap.now?.source)}
        sourceReady={snap.ready}
        ateliers={list}
        onCommand={openDock}
      />
      <Scene
        snap={snap}
        clock={clock}
        since={since}
        on={studio.on}
        starting={studio.starting}
        analyser={studio.analyser}
        error={studio.error}
        onToggle={() => void studio.toggle()}
        voiceover={{
          on: voiceover.on,
          busy: voiceover.busy,
          error: voiceover.error,
          fade: voiceover.fade,
          level: voiceover.level,
          onToggle: () => void voiceover.toggle(),
          onFade: voiceover.setFade,
        }}
      />
      <Path
        snap={snap}
        live={live}
        announce={voiceover.on}
        harborMs={harborMs}
        casqueLabel={casqueLabel}
        casqueMs={casqueMs}
        onMeasure={() => void measure()}
      />
      <CommandK open={dock} ateliers={list} onClose={closeDock} />
    </div>
  );
}
