# ADR-002 — Outlive est la source unique des événements d'Orléans

Date : 2026-09-21. Statut : accepté.

## Décision
Régie ne saisit jamais un événement public à la main. Le module `events` lit Outlive (API v1 partenaire, PostgREST anonyme en secours) et le met en cache localement (`outlive_events`), rafraîchi toutes les 15 minutes. Les seules écritures vers Outlive concernent les événements dont Radio Campus est l'organisateur (`9d6d803d-…`), avec `agenda_types` renseigné.

## Conséquences
Le mappage Outlive → Régie est testé contre un échantillon figé ; un champ inconnu est ignoré et signalé, jamais bloquant. Si Outlive change de schéma, seul le connecteur bouge.
