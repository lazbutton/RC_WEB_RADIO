import { Callout } from "@radix-ui/themes";
import { InfoCircledIcon } from "@radix-ui/react-icons";

export function ProtoNotice() {
  return (
    <Callout.Root color="amber" mb="3">
      <Callout.Icon>
        <InfoCircledIcon />
      </Callout.Icon>
      <Callout.Text>
        Proto sans données — rien n’est persisté, pas de lien avec Radiotomate (:6811).
      </Callout.Text>
    </Callout.Root>
  );
}
