import { useEffect, useRef } from "react";

export type KeyHandler = (ev: KeyboardEvent) => void | boolean;
export type KeyMap = Record<string, KeyHandler>;

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  if (/input|textarea|select/i.test(el.tagName)) return true;
  return Boolean(el.isContentEditable);
}

export function keyName(ev: KeyboardEvent): string {
  const parts: string[] = [];
  if (ev.metaKey || ev.ctrlKey) parts.push("mod");
  if (ev.shiftKey && ev.key.length === 1 && /[a-z]/i.test(ev.key)) parts.push("shift");
  const key = ev.key.length === 1 ? ev.key.toLowerCase() : ev.key;
  parts.push(key);
  return parts.join("+");
}

export function flashKey(key: string): void {
  const target = document.querySelector<HTMLElement>(`[data-key="${key}" i]`);
  if (!target) return;
  target.classList.remove("is-flash");
  void target.offsetWidth;
  target.classList.add("is-flash");
  window.setTimeout(() => target.classList.remove("is-flash"), 320);
}

/** Global shortcuts. Handlers read from a ref so the listener is registered once. */
export function useKeyboard(map: KeyMap, enabled = true): void {
  const ref = useRef(map);
  ref.current = map;
  useEffect(() => {
    if (!enabled) return;
    function onKey(ev: KeyboardEvent) {
      const name = keyName(ev);
      const typing = isTyping(ev.target);
      const handler = ref.current[name];
      if (!handler) return;
      if (typing && !name.startsWith("mod+") && name !== "Escape") return;
      const handled = handler(ev);
      if (handled !== false) {
        ev.preventDefault();
        flashKey(name.replace("shift+", "⇧").replace("mod+", "⌘"));
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [enabled]);
}
