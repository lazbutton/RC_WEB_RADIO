import { Flex, Heading, Text } from "@radix-ui/themes";
import { ProtoNotice } from "../components/ProtoNotice";
import { CLOCKS, clockAtHour } from "../mock/catalog";

const DAYS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"] as const;

export function SemainePage() {
  return (
    <Flex direction="column" gap="3">
      <ProtoNotice />
      <Flex justify="between" align="baseline" wrap="wrap" gap="2">
        <Heading size="5">Semaine</Heading>
        <Text size="2" color="gray">
          Une case = une horloge · 7h–19h {CLOCKS[0].name} · sinon {CLOCKS[1].name}
        </Text>
      </Flex>
      <div className="week-grid" role="grid" aria-label="Grille 7 jours × 24 heures">
        <div className="week-cell week-head" />
        {DAYS.map((day) => (
          <div key={day} className="week-cell week-head" role="columnheader">
            {day}
          </div>
        ))}
        {Array.from({ length: 24 }, (_, hour) => (
          <HourRow key={hour} hour={hour} />
        ))}
      </div>
    </Flex>
  );
}

function HourRow({ hour }: { hour: number }) {
  const label = `${String(hour).padStart(2, "0")}h`;
  const clock = clockAtHour(hour);
  const short = clock.id === "journee" ? "Journée" : "Soir";
  return (
    <>
      <div className="week-cell week-hour">{label}</div>
      {DAYS.map((day) => (
        <div
          key={`${day}-${hour}`}
          className={`week-cell week-slot is-${clock.id}`}
          role="gridcell"
          aria-label={`${day} ${label}, ${clock.name}`}
          title={clock.name}
        >
          {hour % 3 === 0 ? short : ""}
        </div>
      ))}
    </>
  );
}
