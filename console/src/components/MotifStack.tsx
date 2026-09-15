import { Button, Flex, Text, Tooltip } from "@radix-ui/themes";
import { PlusIcon } from "@radix-ui/react-icons";
import { kindLabel, type ClockDef } from "../mock/catalog";

type MotifStackProps = {
  clock: ClockDef;
  onAdd: () => void;
};

export function MotifStack({ clock, onAdd }: MotifStackProps) {
  return (
    <Flex direction="column" gap="2">
      <Flex justify="between" align="center">
        <Text size="2" weight="medium">
          Motif séquentiel — {clock.name}
        </Text>
        <Tooltip content="Proto : pas d’enregistrement">
          <Button size="1" variant="soft" onClick={onAdd}>
            <PlusIcon />
            Position
          </Button>
        </Tooltip>
      </Flex>
      <Flex gap="2" wrap="wrap">
        {clock.motif.map((step, i) => {
          const cible = step.category ?? step.cart ?? "";
          return (
            <button key={`${step.kind}-${i}`} type="button" className="motif-chip" disabled>
              {i + 1}. {kindLabel(step.kind)}
              {cible ? ` · ${cible}` : ""}
            </button>
          );
        })}
      </Flex>
      <Text size="1" color="gray">
        Boucle entre les ancres. Jingle = file jingles (coupe).
      </Text>
    </Flex>
  );
}
