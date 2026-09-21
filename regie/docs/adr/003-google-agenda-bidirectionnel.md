# ADR-003 — Google Agenda bidirectionnel par polling syncToken

Date : 2026-09-21. Statut : accepté (choix de l'équipe).

## Décision
OAuth Google par compte (Laz, Lou), jetons chiffrés en base (AES-GCM, clé dans `REGIE_SECRET`). Lecture incrémentale par `syncToken` toutes les 60 s (Nasgul n'est pas public : pas de webhook). Écriture idempotente via `external_refs`. Conflit : la dernière modification gagne, la version précédente est conservée dans l'historique de l'entité. Garde-fou : au-delà de 20 modifications dans une même passe, la passe s'arrête et demande confirmation. Repli : lecture des ICS privés si le jeton est perdu.

## Conséquences
Une dépendance externe assumée (projet Google Cloud à maintenir). Le connecteur a un faux jumeau complet pour les tests.
