import { Badge, Flex, IconButton, Text, Tooltip } from "@radix-ui/themes";
import {
  CalendarIcon,
  DashboardIcon,
  DoubleArrowLeftIcon,
  DoubleArrowRightIcon,
  MixIcon,
  RowsIcon,
  SpeakerLoudIcon,
  StackIcon,
} from "@radix-ui/react-icons";
import { useCallback, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { parisParts, useParisNow } from "../lib/parisClock";

const NAV = [
  { to: "/antenne", label: "Antenne", icon: DashboardIcon },
  { to: "/horloges", label: "Horloges", icon: MixIcon },
  { to: "/semaine", label: "Semaine", icon: CalendarIcon },
  { to: "/conducteur", label: "Conducteur", icon: RowsIcon },
  { to: "/categories", label: "Catégories", icon: StackIcon },
  { to: "/habillage", label: "Habillage", icon: SpeakerLoudIcon },
] as const;

const NAV_KEY = "app-nav-collapsed";

function readCollapsed(preferCollapsed: boolean): boolean {
  try {
    const stored = window.localStorage.getItem(NAV_KEY);
    if (stored === "1") return true;
    if (stored === "0") return false;
    return preferCollapsed;
  } catch {
    return preferCollapsed;
  }
}

export function Shell() {
  const now = useParisNow();
  const { clock } = parisParts(now);
  const { pathname } = useLocation();
  const desk = pathname === "/antenne";
  const [collapsed, setCollapsed] = useState(() => readCollapsed(desk));

  useEffect(() => {
    if (!desk) return;
    try {
      if (window.localStorage.getItem(NAV_KEY) == null) {
        setCollapsed(true);
      }
    } catch {
      /* proto */
    }
  }, [desk]);

  const toggleNav = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(NAV_KEY, next ? "1" : "0");
      } catch {
        /* proto */
      }
      return next;
    });
  }, []);

  return (
    <div className={`app-shell${collapsed ? " is-nav-collapsed" : ""}`}>
      <aside className="app-sidebar" id="app-sidebar" aria-label="Navigation automate">
        <div className="app-sidebar-head">
          <Link className="app-brand" to="/antenne">
            <img src="/logo.svg" alt="" width={32} height={32} />
            <Flex direction="column" gap="0" className="app-brand-text">
              <Text size="2" weight="bold">
                BUTTON
              </Text>
              <Text size="1" color="gray">
                Automate — proto
              </Text>
            </Flex>
          </Link>
          <Tooltip content={collapsed ? "Déplier le menu" : "Replier le menu"}>
            <IconButton
              variant="ghost"
              color="gray"
              size="1"
              aria-expanded={!collapsed}
              aria-controls="app-sidebar"
              aria-label={collapsed ? "Déplier le menu" : "Replier le menu"}
              onClick={toggleNav}
            >
              {collapsed ? <DoubleArrowRightIcon /> : <DoubleArrowLeftIcon />}
            </IconButton>
          </Tooltip>
        </div>

        <nav className="app-nav" aria-label="Console">
          {NAV.map((item) => {
            const link = (
              <NavLink
                key={item.to}
                to={item.to}
                aria-label={item.label}
                className={({ isActive }) => (isActive ? "is-active" : undefined)}
              >
                <item.icon />
                <span className="app-nav-label">{item.label}</span>
              </NavLink>
            );
            return collapsed ? (
              <Tooltip key={item.to} content={item.label} side="right">
                {link}
              </Tooltip>
            ) : (
              link
            );
          })}
        </nav>

        <div className="app-sidebar-foot">
          <Text size="1" color="gray">
            Squelette sans données
          </Text>
          <Text size="2" as="p" mt="1">
            BUTTON
          </Text>
        </div>
      </aside>

      <div className="app-main">
        <header className="app-topbar">
          {collapsed ? (
            <Tooltip content="Déplier le menu">
              <IconButton
                variant="ghost"
                color="gray"
                size="1"
                aria-expanded={false}
                aria-controls="app-sidebar"
                aria-label="Déplier le menu"
                onClick={toggleNav}
              >
                <DoubleArrowRightIcon />
              </IconButton>
            </Tooltip>
          ) : null}
          <Flex align="center" gap="2">
            <span className="app-auto-dot" aria-hidden="true" />
            <Badge color="green" variant="soft">
              AUTO
            </Badge>
          </Flex>
          <Text size="1" color="gray" style={{ flex: 1 }}>
            Hors live QG — proto, pas :6811
          </Text>
          <Tooltip content="Heure civile Europe/Paris">
            <span className="app-topbar-clock" aria-label="Heure Paris">
              {clock}
            </span>
          </Tooltip>
        </header>
        <div className={`app-page${desk ? " app-page--desk" : ""}`}>
          <Outlet />
        </div>
      </div>
    </div>
  );
}
