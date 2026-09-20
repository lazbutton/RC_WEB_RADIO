# Automate d’antenne — feuille de route

**En pause.** On ne signe pas la phase 0 live / filets QG pour commencer.

Premier chantier en cours : l’automate customisable — [`automate-horloges.md`](automate-horloges.md), chantier [`automate-horloges-plan.md`](automate-horloges-plan.md). Reprendre ce fichier quand les horloges sont tenues.

---

**Quoi faire, dans quel ordre** (chantier **antenne**, plus tard). Sans code.

La spec station : [`automate.md`](automate.md).  
Ce fichier : chantiers live / relais / overlay, livrables, dépendances.

| | |
|---|---|
| Objectif | La grille commande l’antenne, y compris si live ou relais manque |
| Dossier technique | `radiotomate/` (scheduler, modèles) |
| Hors chantier | `player/`, `ops/`, SPA, réécriture Liquidsoap, pupitre, 5e produit |

Les phases sont **bloquantes** : on ne saute pas.

---

## Hors chantier (rappel)

Ne pas élargir en cours de route.

- Site auditeur, Icecast, Caddy, banc Docker
- Console React, FastAPI, nouveau dossier à la racine
- Nouvelle file audio dans Liquidsoap ; repair du crossfade ; ducking
- Mixette, GPIO, droits fins, rebase Heptapod
- Éditeur Quart complet `:6811` (ADR-8) — hors cette feuille, sauf import / relecture minimale en phase 1–5

Playout réel pour la recette : Linux / Labomedia. Le Mac ne fait que l’UI démo.

---

## Vue d’ensemble

| Phase | Nom | Dépend de | Antenne pilotée par la nouvelle grille ? |
|---|---|---|---|
| 0 | Éditorial | — | non |
| 1 | Grille en base (ombre) | 0 | non — ancien trafic continue |
| 2 | Conducteur listable | 1 | non |
| 3 | Interpréteur + **bascule** | 2 | **oui** — un seul *quand* |
| 4 | Fusion des calendriers | 3 | oui |
| 5 | Une vérité visible | 4 | oui |
| 6 | Recette | 3 (idéalement 5) | oui |

Ombre (phases 1–2) : on **écrit** et on **lit** la grille / le conducteur, on ne **commande** pas encore Liquidsoap. Un seul planificateur *actif* jusqu’à la bascule.

---

## Phase 0 — Éditorial

**Aucun travail logiciel** tant que cette phase n’est pas signée (spec §12).

Pour chaque créneau ci-dessous, l’éditorial fixe :

- **ressource** : quel cart, quelle URL Icecast, quel filtre Beets, quel compte `stream` attendu ;
- **sync** : dure ou molle ;
- **filet** : type + cible (cart, filtre auto-DJ, bed) — obligatoire.

Fuseau : `Europe/Paris`. Jeu de tests : ancienne grille NTF#4 (projet festival retiré).

### Overlay NTF#4 — à remplir

Légende filet / sync / ressource : `à trancher` = décision manquante. Ne pas inventer à la place de l’éditorial.

| Quand | Titre | Type | Ressource | Sync | Filet |
|---|---|---|---|---|---|
| Mer 5, 10:00–18:00 | Auto-DJ archives + rotation | autodj | à trancher (filtres `ntf*` / `rotation`) | à trancher | à trancher |
| Mer 5, 18:00–20:00 | Cart ouverture — Zamzamrec | cart | à trancher (id cart) | à trancher | à trancher |
| **Mer 5, 20:00–22:00** | **QG St Aignan — plateau** | **live** | **compte `qg-st-aignan`** | **à trancher — en premier** | **à trancher — en premier** : que sort-on si l’encodeur n’est pas là ? |
| Jeu 6, 10:00–18:00 | Auto-DJ | autodj | à trancher | à trancher | à trancher |
| Jeu 6, 18:00–19:00 | Relais Radio Campus Orléans | relay | à trancher (URL) | à trancher | à trancher |
| Jeu 6, 19:00–21:00 | Interviews artistes | live | à trancher (compte `stream`) | à trancher | à trancher |
| Jeu 6, 21:00–23:00 | Lives enregistrés NTF | cart | à trancher | à trancher | à trancher |
| Ven 7, 10:00–17:00 | Auto-DJ | autodj | à trancher | à trancher | à trancher |
| Ven 7, 17:00–19:00 | Relais Studio Zef | relay | à trancher (URL) | à trancher | à trancher |
| Ven 7, 19:00–23:00 | QG village — David Chouferbad | live | à trancher | à trancher | à trancher |
| Sam 8, 11:00–18:00 | Auto-DJ + jingles BUTTON | autodj | à trancher + cart jingles | à trancher | à trancher |
| Sam 8, 18:00–20:00 | Relais P-Node | relay | à trancher (URL) | à trancher | à trancher |
| Sam 8, 20:00–00:00 | Soirée plateau commun | live | à trancher | à trancher | à trancher |
| Dim 9, 11:00–16:00 | Archives + clôture | cart | à trancher | à trancher | à trancher |
| Dim 9, 16:00–18:00 | Bilan antenne | live | à trancher | à trancher | à trancher |
| Dim 9, 18:00–23:59 | Retour 24/24 | autodj | à trancher | à trancher | à trancher |

**Premier créneau à signer**, avant les autres : mercredi 5 mai 2027, 20:00–22:00, QG absent.

### Semaine 24/24 — à remplir

Un créneau `autodj` qui **couvre toute la semaine** (pas de trou), plus filet. Carts / relais ponctuels hors festival : les ajouter ici quand l’éditorial les connaît.

| Portée | Type | Ressource | Sync | Filet |
|---|---|---|---|---|
| Semaine type, 00:00–24:00 × 7 | autodj | à trancher | molle (défaut proposé, à confirmer) | à trancher |

Horloge v1 (optionnel phase 0) : cart jingles BUTTON + cadence, surtout pour le samedi festival et le 24/24.

### Livrable phase 0

- Tableau overlay **complet** (plus de `à trancher` sur filet / sync / ressource)
- Semaine 24/24 signée
- Liste des carts / URLs / comptes qui **n’existent pas encore** (à créer dans Radiotomate avant ou pendant la phase 1, hors inventaire code)

### On ne touche pas

Aucun fichier logiciel. Pas d’import, pas de scheduler.

---

## Phase 1 — Grille en base (ombre)

**Dépend de :** phase 0 signée.

### Faire

- Persister les créneaux (semaine + overlay) dans le SQLite Radiotomate, même base que carts / users.
- Importer le jeton NTF#4 **plus** les filets / sync signés en phase 0.
- Pouvoir relire la grille depuis la base (contrôle interne, pas une SPA).
- Laisser l’ancien trafic tourner : files vides + cron des carts. La nouvelle grille ne commande pas Liquidsoap.

### Livrable

Grille NTF#4 + semaine 24/24 **lisibles** en base, alignées sur les décisions de la phase 0. Antenne inchangée.

### On ne touche pas

Liquidsoap, remplissage auto-DJ actuel, crons TIMED, `player/`, `ops/`.

---

## Phase 2 — Conducteur listable

**Dépend de :** phase 1.

### Faire

- Matérialiser une fenêtre d’items ≥ 30 min (cible 60) : prévu, type, ressource, sync, statut.
- Recalculer si la grille change.
- Pouvoir **lister** les 30 prochaines minutes (exigence EF-03), encore sans les jouer.
- Prévoir la reprise après restart depuis ces items (pas encore substituée au random Beets).

### Livrable

Conducteur consultable, cohérent avec la grille. Antenne toujours sur l’ancien trafic.

### On ne touche pas

Appels playout, bascule, fusion `AutoDJSlot` / TIMED.

---

## Phase 3 — Interpréteur et bascule

**Dépend de :** phase 2.

C’est le **seul** moment où l’antenne change de maître.

### Faire

- Exécuter le conducteur via les files Liquidsoap **existantes** : carts, auto-DJ, jingles, relais (démarrer / arrêter en fin de créneau), skip.
- Comportements spec §9 : live + filet, relais mort, cart vide, sync dure / molle, harbor qui préempte.
- Jingles poussés par l’horloge, plus le délai magique ~13 min comme politique d’habillage.
- **Bascule :** un seul *quand*. Arrêter la politique « file vide ⇒ tirage Beets au hasard » et les crons TIMED comme calendrier parallèle.

### Livrable

À heure H, le créneau (ou son filet) est ce qui est **prévu** à l’antenne. Question-test mer. 5, 20:00, QG absent : tenue en conditions de test (Linux).

### On ne touche pas

Graphe `fallback` Liquidsoap, crossfade, player, Icecast, nouvelle file audio.

---

## Phase 4 — Fusion des calendriers

**Dépend de :** phase 3 (bascule faite, un seul *quand* actif).

### Faire

- Migrer les créneaux auto-DJ semaine (`AutoDJSlot`) vers la grille unique.
- Migrer les carts à horaire TIMED vers des créneaux `cart` (ou `relay`) de la grille.
- Vérifier qu’il ne reste plus qu’un planificateur de programmation.

### Livrable

Plus de double calendrier métier. Les objets cart restent le *quoi* ; la grille est le *quand*.

### On ne touche pas

Beets / dropbox comme médiathèque, rôles utilisateurs, harbor.

---

## Phase 5 — Une vérité visible

**Dépend de :** phase 4 (grille unique stable).

### Faire

- Import / relecture minimale si besoin (pas d’éditeur complet, pas de SPA). Pas de calendrier parallèle.

### Livrable

Changer la programmation au bon endroit (base) se reflète sur l’affiche équipe.

### On ne touche pas

Player, nouveau site public de grille.

---

## Phase 6 — Recette

**Dépend de :** phase 3 au minimum ; phase 5 pour le critère « une vérité visible ».

Passer les critères d’acceptation de [`automate.md`](automate.md) §10, sur playout Linux / Labomedia :

1. Changement de créneau en base ⇒ antenne prévue changée
2. 30 prochaines minutes listables
3. Live, harbor absent ⇒ filet
4. Relais mort ⇒ filet
5. Cart vide ⇒ filet
6. Harbor autorisé préempte
7. Fin de live : pas de reprise milieu de titre
8. Sync dure et molle
9. Overlay NTF#4 puis retour semaine 24/24
10. Player / Icecast inchangés
11. Pas de 5e produit, pas de nouvelle file LS, pas de second planificateur

### Livrable

Recette cochée (ou écarts documentés hors spec). Mise à jour ponctuelle de [`architecture.md`](architecture.md) **seulement** quand la cible est devenue l’actuel.

### On ne touche pas

Élargir le périmètre parce qu’« on est déjà dans le moteur ».

---

## Ordre si le temps manque avant NTF#4

Tenir **0 → 1 → 2 → 3 → 6** (critères 1–8 et 10–11).  
Phases 4 et 5 : nécessaires pour ne pas revivre deux calendriers, mais la question-test 20:00 se joue à la phase 3.

Sans phase 0 signée : **rien**.
