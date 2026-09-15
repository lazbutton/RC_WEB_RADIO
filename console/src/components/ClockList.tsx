import {
  Button,
  Dialog,
  Flex,
  Heading,
  Text,
  TextField,
} from "@radix-ui/themes";
import { PlusIcon } from "@radix-ui/react-icons";
import { useState } from "react";
import { CLOCKS, type ClockDef } from "../mock/catalog";

type ClockListProps = {
  selectedId: ClockDef["id"];
  onSelect: (id: ClockDef["id"]) => void;
  onProtoSubmit: () => void;
};

export function ClockList({ selectedId, onSelect, onProtoSubmit }: ClockListProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");

  return (
    <Flex direction="column" gap="3" style={{ minWidth: "13.5rem" }}>
      <Flex justify="between" align="center">
        <Heading size="3">Horloges</Heading>
        <Dialog.Root open={open} onOpenChange={setOpen}>
          <Dialog.Trigger>
            <Button size="1">
              <PlusIcon />
              Nouveau
            </Button>
          </Dialog.Trigger>
          <Dialog.Content maxWidth="28rem">
            <Dialog.Title>Nouvelle horloge</Dialog.Title>
            <Dialog.Description size="2" mb="3">
              Nom seulement. Rien n’est enregistré dans ce proto.
            </Dialog.Description>
            <Text as="label" size="2" weight="medium">
              Nom
              <TextField.Root
                mt="1"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Journée avec pubs"
                autoFocus
              />
            </Text>
            <Flex gap="2" mt="4" justify="end">
              <Dialog.Close>
                <Button variant="soft" color="gray">
                  Annuler
                </Button>
              </Dialog.Close>
              <Dialog.Close>
                <Button
                  onClick={() => {
                    onProtoSubmit();
                    setName("");
                  }}
                >
                  Créer
                </Button>
              </Dialog.Close>
            </Flex>
          </Dialog.Content>
        </Dialog.Root>
      </Flex>

      <Flex direction="column" gap="2">
        {CLOCKS.map((clock) => {
          const active = clock.id === selectedId;
          return (
            <button
              key={clock.id}
              type="button"
              className={`clock-pick${active ? " is-selected" : ""}`}
              aria-pressed={active}
              onClick={() => onSelect(clock.id)}
            >
              <Text as="span" size="2" weight="medium">
                {clock.name}
              </Text>
              <Text as="span" size="1" color="gray">
                {clock.anchors.length
                  ? `Ancres :${clock.anchors.map((a) => a.minute).join(" / :")}`
                  : "Motif seul, sans ancre"}
              </Text>
            </button>
          );
        })}
      </Flex>
    </Flex>
  );
}
