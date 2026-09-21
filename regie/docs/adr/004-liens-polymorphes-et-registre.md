# ADR-004 — Liens polymorphes et registre d'entités

Date : 2026-09-21. Statut : accepté.

## Décision
Les relations transverses (mail → contact, événement → épisode, tâche → n'importe quoi) passent par une table `links(src_kind, src_id, dst_kind, dst_id, role)`. Chaque type métier se déclare au registre (`EntityKind` : libellé, icône, résumé, indexation, actions). Les relations internes à un module restent des clés étrangères classiques.

## Conséquences
Ajouter un type = un fichier côté API, un fichier côté interface ; aucun changement de schéma du noyau. En contrepartie, l'intégrité des liens est assurée par le code (`on_delete`, `purge`, `relink`), pas par la base.
