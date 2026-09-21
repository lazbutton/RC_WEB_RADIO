# ADR-005 — Jobs persistants avec bail, plusieurs travailleurs

Date : 2026-09-21. Statut : accepté.

## Décision
La file de jobs vit dans Postgres (`jobs`), prise par bail (`SELECT … FOR UPDATE SKIP LOCKED`), retentatives à délai croissant, file des échecs définitifs (`dead`) visible, planification (`schedules`). Voies (`imap`, `llm`, `google`, `outlive`, `media`, `publish`, `transcribe`, `actions`, `maintenance`) avec un seul travailleur là où l'ordre compte. Un travailleur peut tourner dans un autre processus ou une autre machine (`python -m regie worker transcribe` sur le Mac).

## Conséquences
Un job survit au redémarrage ; un job bloqué expire (bail) et repasse en file ; une action utilisateur passe toujours avant un relevé.
