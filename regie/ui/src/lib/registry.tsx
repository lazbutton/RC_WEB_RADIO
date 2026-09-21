/** Registre d'entités côté interface : libellés, icônes, routes, session et permissions. */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { me as fetchMe, registry as fetchRegistry, type EntitySummary, type Me, type RegistryKind } from "../api/client";
import { Icon, type IconName } from "../components/Icon";

export type Registry = {
  me: Me | null;
  kinds: Record<string, RegistryKind>;
  modules: string[];
  can: (module: string, level?: "read" | "write" | "admin") => boolean;
  refresh: () => Promise<void>;
  routeFor: (kind: string, id: string | number) => string;
};

const LEVELS = ["none", "read", "write", "admin"];
const ROUTES: Record<string, (id: string) => string> = {
  mail: (id) => `/mails?id=${id}`,
  mail_index: (id) => `/mails?mail=${id}`,
  person: (id) => `/contacts/person/${id}`,
  organization: (id) => `/contacts/organization/${id}`,
  place: (id) => `/contacts/place/${id}`,
  event: (id) => `/events/${id}`,
  coverage: () => `/events`,
  task: (id) => `/planning?task=${id}`,
  calendar_event: (id) => `/planning/week?event=${id}`,
  show: (id) => `/shows/${id}`,
  episode: (id) => `/shows/episodes/${id}`,
  segment: () => `/shows`,
  podcast: (id) => `/shows/podcasts/${id}`,
  guest: (id) => `/radio?tab=guests&id=${id}`,
  booking: () => `/radio?tab=bookings`,
  volunteer: () => `/radio?tab=volunteers`,
  partnership: () => `/radio?tab=partnerships`,
  rundown: (id) => `/radio?tab=rundowns&id=${id}`,
  file: (id) => `/files?path=${encodeURIComponent(id)}`,
};

const RegistryContext = createContext<Registry>({ me: null, kinds: {}, modules: [], can: () => false, refresh: async () => undefined, routeFor: () => "/" });

export function RegistryProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [kinds, setKinds] = useState<Record<string, RegistryKind>>({});
  const [modules, setModules] = useState<string[]>([]);

  const refresh = useCallback(async () => {
    const [session, reg] = await Promise.all([fetchMe(), fetchRegistry()]);
    setMe(session);
    setKinds(Object.fromEntries(reg.kinds.map((k) => [k.kind, k])));
    setModules(reg.modules);
  }, []);

  useEffect(() => {
    void refresh().catch(() => undefined);
  }, [refresh]);

  const value = useMemo<Registry>(
    () => ({
      me,
      kinds,
      modules,
      can: (module, level = "read") => {
        if (!me) return false;
        if (me.user.role === "admin") return true;
        const have = me.permissions[module] ?? "none";
        return LEVELS.indexOf(have) >= LEVELS.indexOf(level);
      },
      refresh,
      routeFor: (kind, id) => (ROUTES[kind] ? ROUTES[kind](String(id)) : "/"),
    }),
    [me, kinds, modules, refresh],
  );
  return <RegistryContext.Provider value={value}>{children}</RegistryContext.Provider>;
}

export function useRegistry(): Registry {
  return useContext(RegistryContext);
}

const KNOWN_ICONS = new Set<string>(["mail", "user", "users", "building", "pin", "calendar", "mic", "radio", "disc", "scissors", "headphones", "list", "handshake", "key", "file", "check", "clock", "dot"]);

export function kindIcon(kind: string, fallback?: string): IconName {
  const byKind: Record<string, IconName> = { mail: "mail", mail_index: "mail", person: "user", organization: "building", place: "pin", event: "calendar", coverage: "mic", task: "check", calendar_event: "clock", show: "radio", episode: "disc", segment: "scissors", podcast: "headphones", guest: "mic", booking: "key", volunteer: "users", partnership: "handshake", rundown: "list", file: "file" };
  if (byKind[kind]) return byKind[kind];
  if (fallback && KNOWN_ICONS.has(fallback)) return fallback as IconName;
  return "dot";
}

/** Puce d'entité : icône du type, titre, sous-titre, lien vers la fiche. */
export function EntityChip({ entity, compact = false }: { entity: EntitySummary | null | undefined; compact?: boolean }) {
  const reg = useRegistry();
  if (!entity) return <span className="entity-chip is-missing">(inconnu)</span>;
  const to = entity.url || reg.routeFor(entity.kind, entity.id);
  return (
    <Link to={to} className={`entity-chip${entity.missing ? " is-missing" : ""}${compact ? " is-compact" : ""}`} title={entity.subtitle || entity.title}>
      <Icon name={kindIcon(entity.kind, entity.icon)} size={14} />
      <span className="entity-title">{entity.title}</span>
      {!compact && entity.subtitle ? <span className="entity-sub">{entity.subtitle}</span> : null}
    </Link>
  );
}
