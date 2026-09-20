import { Card, Flex, Grid, Heading, Text } from "@radix-ui/themes";
import { ProtoNotice } from "../components/ProtoNotice";
import { formatMmSs } from "../lib/parisClock";
import { itemLabel, itemsByCart } from "../mock/catalog";

const BANKS = [
  { title: "Jingles", cart: "Jingles" as const, hint: "File jingles LS" },
  { title: "Pubs", cart: "Pubs" as const, hint: "Ancres :20 / :40" },
  { title: "Promos / sons", cart: "Promos" as const, hint: "Beds, virgules, promos" },
] as const;

export function HabillagePage() {
  return (
    <Flex direction="column" gap="3">
      <ProtoNotice />
      <Heading size="5">Habillage</Heading>
      <Text size="2" color="gray">
        Carts mock — mêmes IDs que les pads Antenne.
      </Text>
      <Grid columns={{ initial: "1", md: "3" }} gap="3">
        {BANKS.map((bank) => {
          const items = itemsByCart(bank.cart);
          return (
            <Card key={bank.cart} size="2">
              <Heading size="3">{bank.title}</Heading>
              <Text size="1" color="gray">
                {bank.hint} · {items.length} carts
              </Text>
              <Flex direction="column" gap="2" mt="3">
                {items.map((item) => (
                  <Flex key={item.id} justify="between" gap="2">
                    <Text size="2">{itemLabel(item)}</Text>
                    <Text size="1" color="gray" style={{ fontVariantNumeric: "tabular-nums" }}>
                      {formatMmSs(item.durationSec)}
                    </Text>
                  </Flex>
                ))}
              </Flex>
            </Card>
          );
        })}
      </Grid>
    </Flex>
  );
}
