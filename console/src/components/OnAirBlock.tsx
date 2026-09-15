import { Badge, Flex, Text } from "@radix-ui/themes";
import { itemLabel, kindLabel, type CatalogItem } from "../mock/catalog";

export function OnAirBlock({
  label,
  item,
}: {
  label: string;
  item: CatalogItem | undefined;
}) {
  return (
    <Flex direction="column" gap="1" className="onair-block">
      <Text size="1" color="gray" weight="medium">
        {label}
      </Text>
      {item ? (
        <>
          <Badge size="1" variant="soft">
            {kindLabel(item.kind)}
          </Badge>
          <Text size="3" weight="medium">
            {item.artist ?? item.title}
          </Text>
          {item.artist ? (
            <Text size="2" color="gray">
              {item.title}
            </Text>
          ) : (
            <Text size="1" color="gray">
              {item.cart}
            </Text>
          )}
        </>
      ) : (
        <Text size="2" color="gray">
          —
        </Text>
      )}
    </Flex>
  );
}

export function CueValue({ item }: { item: CatalogItem | undefined }) {
  return <Text size="2">{item ? itemLabel(item) : "—"}</Text>;
}
