import { Flex, Heading, Text } from "@radix-ui/themes";
import { ConducteurPanel } from "../components/ConducteurPanel";
import { OnAirBlock } from "../components/OnAirBlock";
import { ProtoNotice } from "../components/ProtoNotice";
import { formatMmSs } from "../lib/parisClock";
import { useStation } from "../mock/StationContext";

export function ConducteurPage() {
  const station = useStation();
  const now = station.nowItem?.item;
  const next = station.nextItem?.item;

  return (
    <Flex direction="column" gap="3">
      <ProtoNotice />
      <Heading size="5">Conducteur</Heading>
      <Text size="2" color="gray">
        Même session que le pupitre — horloge « {station.clock.name} ».
      </Text>

      <Flex gap="3" wrap="wrap">
        <OnAirBlock label="Now" item={now} />
        <OnAirBlock label="Next" item={next} />
        <Text size="2" color="gray">
          Restant {formatMmSs(station.remainingSec)}
        </Text>
      </Flex>

      <ConducteurPanel />
    </Flex>
  );
}
