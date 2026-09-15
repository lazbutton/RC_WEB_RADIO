import { Button, Flex, Progress, Text } from "@radix-ui/themes";
import { TrackNextIcon } from "@radix-ui/react-icons";
import { formatMmSs } from "../lib/parisClock";
import { kindLabel, type CatalogItem } from "../mock/catalog";
import { TrackCopy } from "./TrackCopy";

export function DeskNow({
  item,
  elapsedSec,
  durationSec,
  remainingSec,
  onSkip,
}: {
  item: CatalogItem | undefined;
  elapsedSec: number;
  durationSec: number;
  remainingSec: number;
  onSkip: () => void;
}) {
  const ending = remainingSec > 0 && remainingSec <= 10;
  const pct =
    durationSec > 0 ? Math.min(100, Math.round((elapsedSec / durationSec) * 100)) : 0;
  const kindClass = item ? ` is-${item.kind}` : "";

  return (
    <div className={`desk-now${ending ? " is-ending" : ""}${kindClass}`}>
      <Flex className="desk-now-row" align="center" gap="4" wrap="nowrap">
        <div className="desk-now-copy">
          <Text size="1" color="gray" weight="medium">
            Now
          </Text>
          {item ? (
            <div className="desk-now-titles">
              <span
                className={`kind-dot is-${item.kind}`}
                title={kindLabel(item.kind)}
                aria-hidden="true"
              />
              <TrackCopy item={item} size="now" />
            </div>
          ) : (
            <Text as="p" size="8">
              —
            </Text>
          )}
        </div>
        <div className="desk-now-side">
          <span className={`desk-remain${ending ? " is-ending" : ""}`} aria-label="Restant">
            {formatMmSs(remainingSec)}
          </span>
          <Button color="red" size="3" onClick={onSkip}>
            <TrackNextIcon />
            Passer
          </Button>
        </div>
      </Flex>
      <div className="desk-now-progress">
        <Progress value={pct} size="2" />
      </div>
    </div>
  );
}
