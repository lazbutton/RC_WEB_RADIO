# Revue de sécurité — Régie

Date : 2026-09-21. Périmètre : `regie/` déployé sur Nasgul.

## En place

- **Exposition** : aucune entrée publique. Port 30130 sur le NAS, accessible via LAN et Tailscale ; Postgres 30131 lié à l’IP Tailscale seulement (worker Mac). Le bord public est statique et régénérable.
- **Authentification** : un compte par personne, mots de passe scrypt (n=2¹⁵, sel 16 o), 10 caractères minimum, limitation à 8 essais/minute/IP, sessions 30 jours (jeton haché SHA-256 en base, cookie `HttpOnly`, `SameSite=Lax`), désactivation immédiate des sessions à la désactivation du compte, journal `auth_events`.
- **Autorisation** : RBAC par module (`none/read/write/admin`), vérifié dans chaque route (`require(module, level)`), admin seul pour comptes, droits, ressources, effacement RGPD.
- **CSRF** : en-tête `X-Regie: 1` obligatoire sur toute requête mutante + contrôle d’`Origin`. Les flux (Atom, Markdown, RSS local) utilisent un jeton porteur dédié, jamais la session.
- **En-têtes** : CSP stricte sur l’interface, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`. Blocs iframe publics avec leur propre CSP (`default-src 'none'`).
- **Secrets** : variables dans `secrets.env` (600, hors dépôt) ; jetons OAuth et Notion chiffrés AES-GCM en base avec clé dérivée de `REGIE_SECRET` ; jamais de secret dans les logs (les erreurs `pg_dump` sont nettoyées), l’export complet exclut secrets, sessions et hachés.
- **Fichiers** : accès au dataset médias sous racine, chemins résolus et vérifiés (`../` refusé), écriture sur liste blanche (`00-inbox`, `40-emissions`), aucune suppression, jamais d’exécution.
- **Pièces jointes mail** : inspection du contenu (PDF/JPEG/WAV, refus MZ/ZIP polyglottes, extensions doubles), texte des PDF extrait sans JavaScript.
- **Mail** : jamais d’envoi (ADR-007). Drapeaux et déplacements IMAP réversibles, jamais de suppression ni d’expunge non contrôlé.
- **Écritures externes** : idempotentes (`external_refs`), coupe-circuit, garde-fou de 20 modifications en masse vers Google avec confirmation humaine, droits bloquants avant publication d’un podcast.
- **Données** : sauvegarde chiffrée quotidienne + snapshots ZFS, exercice de restauration exécuté avec succès sur le Mac (21/09/2026), export complet en JSON.
- **Conteneurs** : utilisateurs non root (568 / postgres), `no-new-privileges`, limites mémoire, image reconstruite à chaque déploiement, retour arrière automatique si la fumée échoue.

## Risques résiduels et suites

1. **HTTP en clair sur Tailscale/LAN** : le trafic est chiffré par Tailscale mais pas sur le LAN. Suite : servir en HTTPS derrière le certificat Tailscale (`tailscale cert`) et passer le cookie en `Secure`.
2. **Pas de second facteur** : les passkeys sont prévues (ADR à écrire) ; en attendant, mots de passe longs et comptes nominatifs.
3. **Clé `REGIE_SECRET` unique** : sa perte rend les sauvegardes chiffrées et les jetons illisibles. Suite : la consigner dans le gestionnaire de mots de passe de l’association, hors du NAS.
4. **Anthropic reçoit des extraits de mails** : documenté dans le registre ; possibilité de couper (`ANTHROPIC_API_KEY` vide → tri heuristique).
5. **Postgres 30131 exposé sur l’IP Tailscale** : nécessaire au worker Mac. Suite : restreindre par `pg_hba` à l’IP Tailscale du Mac si le besoin se confirme.
6. **Dépendances** : Python et npm à mettre à jour trimestriellement (`pip list --outdated`, `npm audit`).
