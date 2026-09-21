# ADR-006 — Aucune entrée publique sur Nasgul, publication statique

Date : 2026-09-21. Statut : accepté.

## Décision
Régie n'est joignable que par Tailscale. Tout ce qui doit être public (blocs iframe du site, flux RSS, JSON) est **publié** par le module `publish` vers un hébergement statique (Vercel, déjà utilisé pour l'agenda, ou Cloudflare Pages). Le NAS pousse ; personne ne tire.

## Conséquences
Pas de Funnel, pas de reverse proxy exposé, pas de surface d'attaque publique. La perte du bord public se répare par une régénération complète.
