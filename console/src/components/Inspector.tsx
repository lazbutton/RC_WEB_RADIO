import {
  Box,
  Callout,
  Flex,
  Heading,
  Select,
  Separator,
  Switch,
  Text,
  TextField,
} from "@radix-ui/themes";
import { InfoCircledIcon } from "@radix-ui/react-icons";
import type { ClockDef } from "../mock/catalog";
import type { StudioSelection } from "./HourStrip";

type InspectorProps = {
  selection: StudioSelection;
  clock: ClockDef;
};

export function Inspector({ selection, clock }: InspectorProps) {
  if (selection.kind === "idle") {
    return (
      <Flex direction="column" gap="3">
        <Heading size="4">Inspecteur</Heading>
        <Callout.Root color="gray" variant="surface">
          <Callout.Icon>
            <InfoCircledIcon />
          </Callout.Icon>
          <Callout.Text>
            Horloge « {clock.name} ». Quatre types : musique, jingle, son, pub. Séquentiel dans le
            remplissage, ou ancré à la minute, sync dure ou molle.
          </Callout.Text>
        </Callout.Root>
      </Flex>
    );
  }

  const isAnchor = selection.kind === "anchor";
  const hasAnchor = clock.anchors.some((a) => a.minute === (isAnchor ? selection.minute : -1));
  const title = isAnchor ? `Ancre :${selection.minute}` : `Motif ${selection.zone}`;
  const quoi = isAnchor ? "pub" : "musique";
  const cible = isAnchor ? "Pubs" : "Rotation";

  return (
    <Flex direction="column" gap="3">
      <Heading size="4">{title}</Heading>
      {isAnchor && !clock.anchors.length ? (
        <Callout.Root color="amber" variant="surface">
          <Callout.Text>
            Cette horloge n’a pas d’ancre — les pubs :20 / :40 sont sur « Journée avec pubs ».
          </Callout.Text>
        </Callout.Root>
      ) : null}
      <Text size="2" color="gray">
        Modèle {clock.name} — lecture seule, proto sans enregistrement.
      </Text>
      <Separator size="4" />

      <Text as="label" size="2" weight="medium">
        Quoi
        <Box mt="1">
          <Select.Root key={`${title}-quoi`} defaultValue={quoi} disabled>
            <Select.Trigger style={{ width: "100%" }} />
            <Select.Content>
              <Select.Item value="musique">Musique (catégorie Beets)</Select.Item>
              <Select.Item value="jingle">Jingle (cart)</Select.Item>
              <Select.Item value="son">Son (cart)</Select.Item>
              <Select.Item value="pub">Pub (cart)</Select.Item>
            </Select.Content>
          </Select.Root>
        </Box>
      </Text>

      <Text as="label" size="2" weight="medium">
        Quand
        <Box mt="1">
          <Select.Root key={`${title}-quand`} defaultValue={isAnchor ? "ancre" : "seq"} disabled>
            <Select.Trigger style={{ width: "100%" }} />
            <Select.Content>
              <Select.Item value="seq">Séquentiel (remplissage)</Select.Item>
              <Select.Item value="ancre">Ancré (minute d’heure)</Select.Item>
            </Select.Content>
          </Select.Root>
        </Box>
      </Text>

      {isAnchor ? (
        <>
          <Text as="label" size="2" weight="medium">
            Minute
            <TextField.Root mt="1" value={String(selection.minute)} disabled />
          </Text>
          <Text as="label" size="2">
            <Flex align="center" gap="2">
              <Switch defaultChecked={hasAnchor || !clock.anchors.length} disabled />
              Sync dure (couper pour coller à l’heure)
            </Flex>
          </Text>
        </>
      ) : null}

      <Text as="label" size="2" weight="medium">
        Cible
        <TextField.Root mt="1" value={cible} disabled />
      </Text>

      <Text as="label" size="2" weight="medium">
        Secours
        <TextField.Root mt="1" value="Jingles" disabled />
      </Text>
    </Flex>
  );
}
