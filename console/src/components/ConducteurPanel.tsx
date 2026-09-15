import { useMemo } from "react";
import { Flex, Heading, Text } from "@radix-ui/themes";
import { formatMmSs, nextAnchor, useParisNow } from "../lib/parisClock";
import { QueueEditList } from "./QueueEditList";
import { useStation } from "../mock/StationContext";

export function ConducteurPanel({ compact = false }: { compact?: boolean }) {
  const station = useStation();
  const wall = useParisNow();
  const anchor = nextAnchor(wall);
  const playedUids = useMemo(
    () => new Set(station.history.map((row) => row.uid)),
    [station.history],
  );

  return (
    <div className={`conductor-panel${compact ? " is-compact" : ""}`}>
      <Flex justify="between" align="baseline" mb="2" gap="2">
        {compact ? <Heading size="4">Conducteur</Heading> : <span />}
      </Flex>

      <Text size="2" weight="medium" mb="2" as="p" className="conductor-legend">
        Ancre suivante {anchor.label} · {formatMmSs(anchor.remainingSec)}
      </Text>

      <div className="conductor-rail" aria-label="Fenêtre 30 minutes">
        <span
          className="conductor-anchor"
          style={{ left: anchor.label === ":20" ? "40%" : "80%" }}
        />
        <div className="conductor-rail-marks">
          <Text size="2" weight="medium">
            :00
          </Text>
          <Text size="2" weight="medium">
            :20
          </Text>
          <Text size="2" weight="medium">
            :40
          </Text>
        </div>
      </div>

      <QueueEditList
        items={station.conductor}
        mode="window"
        nowUid={station.nowItem?.uid}
        nextUid={station.nextItem?.uid}
        playedUids={playedUids}
        onMove={station.moveInQueue}
        onRemove={station.removeFromQueue}
      />
    </div>
  );
}
