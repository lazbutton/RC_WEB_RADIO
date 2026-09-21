# Registre des traitements — Régie (Radio Campus Orléans)

Responsable : l’association Radio Campus Orléans. Hébergement : NAS Nasgul (locaux de la radio), accès par Tailscale et compte nominatif uniquement. Aucun transfert vers un tiers hors des connecteurs listés.

| Traitement | Données | Personnes | Finalité | Base | Durée | Destinataires / sous-traitants |
| --- | --- | --- | --- | --- | --- | --- |
| Boîte mail (module Mails) | Expéditeur, objet, corps, pièces jointes, drapeaux | Correspondants de la radio | Trier, résumer, relier au travail éditorial | Intérêt légitime (gestion associative) | Tant que le mail existe dans la boîte ; cache local des pièces purgé | Anthropic (extraits envoyés pour le tri et les récaps), Notion (si l’utilisateur crée une tâche) |
| Contacts | Nom, e-mails, téléphones, fonction, structure, notes, interactions | Interlocuteurs (artistes, structures, presse, institutions) | Carnet d’adresses de la radio, historique des échanges | Intérêt légitime | Revue annuelle ; `retention_until` ; effacement sur demande (bouton « Effacer (RGPD) ») | Aucun |
| Comptes Régie | E-mail, nom, mot de passe haché (scrypt), sessions, journal de connexion | Équipe et volontaires | Authentification, droits | Exécution du contrat associatif | Compte désactivé à la sortie ; journal 90 jours | Aucun |
| Agendas Google | Événements (titre, horaires, lieu, participants), jeton OAuth chiffré | Laz, Lou, agenda commun | Planning partagé | Consentement (connexion OAuth révocable) | Jusqu’à retrait de l’agenda | Google (API Calendar) |
| Événements Outlive | Données publiques d’événements | Organisateurs, artistes (données publiques) | Couverture éditoriale | Intérêt légitime | Cache rafraîchi ; événements disparus marqués | Outlive (lecture) |
| Invités | Nom, sujet, autorisation d’enregistrement, PDF signé | Invités de l’antenne | Gestion des invitations et des droits de diffusion | Consentement (autorisation signée) | Durée de vie du podcast + 5 ans (preuve) | Aucun |
| Volontaires | Identité (via contact), mission, dates, heures, attestation | Bénévoles, services civiques, stagiaires | Suivi et attestations | Exécution du contrat / obligation légale | Fin d’engagement + 5 ans | Aucun |
| Podcasts et publications | Titres, descriptions, fichiers audio, URL | Auditeurs (aucune donnée), invités (voix) | Diffusion | Intérêt légitime, autorisation invités | Selon droits vérifiés | WordPress (site), Vercel/Cloudflare (bord public) |
| Statistiques d’écoute | Nombre d’auditeurs par flux (agrégé, aucune IP) | — | Mesure d’audience | Intérêt légitime | 90 jours | Aucun |
| Notifications Web Push | Point de terminaison navigateur, clés | Équipe | Alertes | Consentement | Jusqu’à désabonnement | Service push du navigateur |

Droits des personnes : accès et export par la fiche (JSON), rectification par l’équipe, effacement par le bouton dédié (la fiche, ses interactions et ses liens ; les mails restent dans la boîte). Sauvegardes chiffrées conservées 30 jours puis effacées ; snapshots ZFS 8 semaines.
