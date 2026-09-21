# ADR-001 — Monolithe modulaire FastAPI + Postgres sur Nasgul

Date : 2026-09-21. Statut : accepté.

## Contexte
Deux personnes, un NAS, des besoins qui changent. Il faut un outil autonome, simple à exploiter, mais que l'on puisse étendre pendant des années.

## Décision
Un seul processus `regie-api` (FastAPI, Python 3.12), un seul Postgres dédié (`regie-pg`), une seule interface React. Le code est découpé en un **noyau** sans métier (comptes, registre d'entités, liens, actions réversibles, jobs, connecteurs, outbox, recherche, notifications, fichiers, observabilité) et des **modules** étanches qui ne se parlent qu'à travers le noyau. Les migrations sont des fichiers SQL numérotés (`migrations/NNNN_nom.sql`), jamais modifiés après déploiement, appliqués par `python -m regie migrate` (pas d'ORM, pas d'Alembic : moins de dépendances, même style qu'Outlive).

## Conséquences
Déploiement et sauvegarde triviaux (`docker compose`, `pg_dump`). Découper en services plus tard reste possible module par module. Le prix : discipline sur les frontières (un module n'importe jamais un autre module).
