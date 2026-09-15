import { Callout } from "@radix-ui/themes";
import { useEffect, useState } from "react";

export function useProtoToast() {
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!message) return;
    const id = window.setTimeout(() => setMessage(null), 2600);
    return () => window.clearTimeout(id);
  }, [message]);

  return { message, show: setMessage };
}

export function ProtoToast({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="proto-toast">
      <Callout.Root color="amber" role="status">
        <Callout.Text>{message}</Callout.Text>
      </Callout.Root>
    </div>
  );
}
