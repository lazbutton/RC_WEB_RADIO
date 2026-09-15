import { Card, Heading, ScrollArea, Text } from "@radix-ui/themes";
import { useState } from "react";
import { ClockList } from "../components/ClockList";
import { HourStrip, type StudioSelection } from "../components/HourStrip";
import { Inspector } from "../components/Inspector";
import { MotifStack } from "../components/MotifStack";
import { ProtoNotice } from "../components/ProtoNotice";
import { ProtoToast, useProtoToast } from "../components/ProtoToast";
import { CLOCKS, type ClockDef } from "../mock/catalog";

export function HorlogesPage() {
  const [selection, setSelection] = useState<StudioSelection>({ kind: "idle" });
  const [clockId, setClockId] = useState<ClockDef["id"]>("journee");
  const { message, show } = useProtoToast();
  const clock = CLOCKS.find((c) => c.id === clockId) ?? CLOCKS[0];

  return (
    <>
      <ProtoNotice />
      <div className="ntr-studio">
        <ClockList selectedId={clockId} onSelect={setClockId} onProtoSubmit={() => show("Proto sans données")} />

        <div className="ntr-studio-mid">
          <Heading size="3">Atelier d’heure</Heading>
          <Text size="1" color="gray">
            {clock.name}
            {clock.anchors.length ? " · rails :20 / :40 → cart Pubs" : " · sans ancre pub"}
          </Text>
          <ScrollArea type="hover" scrollbars="vertical" style={{ flex: 1, minHeight: "28rem" }}>
            <HourStrip selection={selection} onSelect={setSelection} />
          </ScrollArea>
          <MotifStack clock={clock} onAdd={() => show("Proto sans données")} />
        </div>

        <Card size="2">
          <Inspector selection={selection} clock={clock} />
        </Card>
      </div>
      <ProtoToast message={message} />
    </>
  );
}
