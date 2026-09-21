# ADR-007 — Jamais d'envoi d'e-mail

Date : 2026-09-21. Statut : accepté.

## Décision
Régie lit et classe la boîte Roundcube (IMAP, PEEK, drapeaux et déplacements réversibles) mais n'envoie jamais d'e-mail : pas de SMTP, pas de notification par mail. Les notifications passent par l'interface (SSE) et le Web Push auto-hébergé (VAPID). Les réponses se rédigent comme exemples à coller dans le webmail.

## Conséquences
Aucun risque d'envoi involontaire, pas de réputation d'expéditeur à gérer, pas de mot de passe SMTP à protéger.
