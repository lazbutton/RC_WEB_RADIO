import { useEffect, useState } from "react";
import { clockLabel } from "./format";

export function useClock() {
  const [label, setLabel] = useState(clockLabel);
  useEffect(() => {
    const id = window.setInterval(() => setLabel(clockLabel()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return label;
}
