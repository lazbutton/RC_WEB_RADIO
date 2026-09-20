import { Badge, Card, Flex, Text } from "@radix-ui/themes";
import { ConducteurPanel } from "../components/ConducteurPanel";
import { DeskNow } from "../components/DeskNow";
import { PadRail } from "../components/PadRail";
import { QueueEditList } from "../components/QueueEditList";
import { TrackCopy } from "../components/TrackCopy";
import { formatMmSs, nextAnchor, useParisNow } from "../lib/parisClock";
import { kindLabel } from "../mock/catalog";
import { useStation } from "../mock/StationContext";

export function AntennePage() {
  const wall = useParisNow();
  const anchor = nextAnchor(wall);
  const {
    clock,
    nowItem,
    elapsedSec,
    remainingSec,
    nowDurationSec,
    history,
    upcoming,
    skip,
    moveInQueue,
    removeFromQueue,
  } = useStation();

  const now = nowItem?.item;
  const previous = history.at(-1)?.item;

  return (
    <div className="desk">
      <div className="desk-main">
        <div className="desk-time">
          <Text size="3" weight="medium" style={{ fontVariantNumeric: "tabular-nums" }}>
            Ancre {anchor.label} · {formatMmSs(anchor.remainingSec)}
          </Text>
          <Badge variant="outline">{clock.name}</Badge>
          <Flex align="center" gap="2" ml="auto">
            <span className="app-auto-dot" aria-hidden="true" />
            <Badge color="green" variant="soft">
              AUTO
            </Badge>
          </Flex>
        </div>

        <div className="desk-stage">
          <div className="desk-col">
            <Card size="2" className="desk-now-card">
              <DeskNow
                item={now}
                elapsedSec={elapsedSec}
                durationSec={nowDurationSec}
                remainingSec={remainingSec}
                onSkip={skip}
              />
            </Card>

            <Card size="2" className="desk-file">
              <div className={`desk-prev${previous ? ` is-${previous.kind}` : ""}`}>
                <Text size="1" color="gray">
                  Précédent
                </Text>
                {previous ? (
                  <div className="desk-file-row">
                    <span
                      className={`kind-dot is-${previous.kind}`}
                      title={kindLabel(previous.kind)}
                      aria-hidden="true"
                    />
                    <TrackCopy item={previous} />
                    <span className="desk-upcoming-dur">{formatMmSs(previous.durationSec)}</span>
                  </div>
                ) : (
                  <Text size="2" color="gray">
                    —
                  </Text>
                )}
              </div>
              <QueueEditList
                items={upcoming}
                mode="upcoming"
                onMove={moveInQueue}
                onRemove={removeFromQueue}
              />
            </Card>
          </div>

          <Card size="2" className="desk-conductor">
            <ConducteurPanel compact />
          </Card>
        </div>
      </div>

      <PadRail />
    </div>
  );
}
