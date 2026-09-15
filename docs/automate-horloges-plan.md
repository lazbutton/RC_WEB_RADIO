# Horloges — feuille de route

**Quoi faire, dans quel ordre.** Spec : [`automate-horloges.md`](automate-horloges.md).

Deux pistes distinctes. Le proto **informe** la phase 0 ; il ne la **signe** pas et ne tient pas les phases moteur.

| Piste | Statut | Livrable réel |
|---|---|---|
| **Proto console** (`console/`, ADR-H7) | En cours, démo | Pupitre `/antenne` + maquettes horloges / semaine / carts. Catalogue mock, pas d’audio, pas d’API. |
| **Phase 0 éditorial** | Non signé | Les horloges mock Journée / Soir sont une **proposition d’écran**, pas les positions / secours / dayparts remplis. |
| **Phases 1–5 moteur** | Pas commencées | Rien dans `radiotomate/` ni Liquidsoap. Le proto ne compte pas pour « le motif tourne » ni « à :20 la pub part ». |

Moteur antenne (live / relais / filets QG) : [`automate-plan.md`](automate-plan.md), **en pause**.

Hors chantier : harbor, `player/`, `ops/`, SPA prod, nouvelle file Liquidsoap, ducking, MIDI.

Les phases **moteur** 0–5 restent **bloquantes**. Le proto peut avancer en parallèle.

```text
proto console  -.->  (informe, ne signe pas)  -->  phase 0
phase 0  -->  phases 1–5 moteur
```

---

## Piste proto — console

Dossier de design, pas `:6811`. Lancer : [`console/README.md`](../console/README.md).

### Fait

- Pupitre `/antenne` : Now (artiste / titre, restant, skip), file ~10 titres, DnD + suppression (Now et ancres verrouillés), conducteur lisible (rail :00 / :20 / :40), nav et pads repliables.
- Rail pads : banques Sons / Jingles / Pubs (grille 2×3), fire → `insertNow`. Switch « Jingle après le son » : arme la banque Jingles, **n’insère pas encore** le couple son+jingle (`insertSonThenJingle` existe, non branché).
- Catalogue mock partagé : catégories Rotation / Archives, carts Jingles NTR / Pubs / Promos, deux horloges **hypothèses** dans [`console/src/mock/catalog.ts`](../console/src/mock/catalog.ts) — Journée (motif + ancres :20 / :40 dures), Soir (motif seul).
- `/horloges` `/semaine` `/categories` `/habillage` `/conducteur` : lecture + toasts « proto ». MIDI APC Mini : absent.

### Prochaines slices (proto seulement)

1. **File = horloge** — aujourd’hui la file est une playlist mock ; le rail d’ancres du conducteur est l’heure civile, pas le motif qui a tiré les items. `buildQueue` doit suivre le motif + ancres de l’horloge du daypart (Journée le jour, Soir le soir). Sans ça, `/antenne` et `/horloges` restent deux démos.
2. **Atelier `/horloges` éditable en mémoire** — inspecteur aujourd’hui disabled / toast. Changer motif et ancres des deux horloges mock, voir l’effet sur file + conducteur. Sert la phase 0 (montrer le modèle) sans SQL.
3. **Chaîne pads réelle** — le switch enfile son puis jingle (brancher `insertSonThenJingle`, ou armer + insert au fire). MIDI / APC Mini : **plus tard**, mapping layout, pas un chantier de cette feuille.

### On ne touche pas (proto)

Audio, files LS, Beets réel, persistance disque, Quart, auth, player, festival, ops.

---

## Phase 0 — Éditorial

**Statut :** non signé. **Aucun code moteur** tant que 1–2 horloges ne sont pas décrites.

Le proto Journée / Soir **n’exonère pas** la signature papier : quoi / minute / sync / secours / dayparts.

Pour chaque horloge : positions (**quoi** / séquentiel ou **minute** / **sync** si ancré), **secours** (cart ou catégorie si la cible est vide). Carts jingles, pubs, promos : existants ou à créer.

Si une horloge n’a que des ancres : **catégorie de remplissage** obligatoire ([`automate-horloges.md`](automate-horloges.md) §4 et §10).

Minimum pour débloquer la suite :

- une horloge **sans** ancre (ex. 24/24 rotation habillée) ;
- une horloge **avec** pubs à :20 et :40 (sync dure), plus un motif de remplissage.

Dayparts : quelle plage de la semaine charge quelle horloge (ex. journée avec pubs, soir sans).

### Livrable

Les deux horloges + l’affectation daypart, plus de cases vides sur quoi / quand / sync / secours.

### On ne touche pas

Moteur, filets QG, grille festival. Le proto peut continuer.

---

## Phase 1 — Motif séquentiel

**Statut :** pas commencé. **Dépend de :** phase 0 (au moins l’horloge sans ancre).

Catégories Beets + types `musique` / `jingle` / `son` / `pub` en **séquentiel seulement**. L’horloge boucle. Pas d’ancres.

Une file mock dans `console/` **ne tient pas** ce livrable.

### Livrable

Le motif tourne : jingles intercalés, pas un random de daypart. L’ancien `delay(780.)` peut encore exister jusqu’à la bascule (phase 4).

### On ne touche pas

Ancres, file carts pour stopsets, player.

---

## Phase 2 — Ancres, sync, remplissage

**Statut :** pas commencé. **Dépend de :** phase 1.

Minutes 0–59, sync dure / molle, remplissage. Pub/son ancré dur → file carts. Respecter les cas limites du cahier §10 (daypart court, molle vs dure, pas deux ancres à la même minute).

### Livrable

À :20, la pub part selon la sync signée. Entre :00 et :20, le motif de la phase 1 continue.

### On ne touche pas

SPA, graphe `fallback`, Icecast.

---

## Phase 3 — Conducteur listable

**Statut :** pas commencé. **Dépend de :** phase 2.

Fenêtre ≥ 30 min : items remplis, y compris la **prochaine ancre** (ex. pub à :20). Recalcul si l’horloge ou le daypart change.

Le conducteur proto (file de session) **ne tient pas** ce livrable.

### Livrable

On peut lire les 30 prochaines minutes sans être à côté du lecteur.

### On ne touche pas

Bascule de l’ancien auto-DJ.

---

## Phase 4 — Bascule

**Statut :** pas commencé. **Dépend de :** phase 3.

Un seul *quoi* pour l’auto-DJ : l’horloge du daypart. Plus de politique « file vide ⇒ Beets au hasard » ni `delay(780.)` comme cadence d’habillage.

### Livrable

Daypart avec horloge à pubs / daypart sans pubs : comportements distincts, comme le cahier.

### On ne touche pas

Live, relais, overlay festival.

---

## Phase 5 — Recette

**Statut :** pas commencé. **Dépend de :** phase 4. Playout **Linux / Labomedia**.

Critères de [`automate-horloges.md`](automate-horloges.md) §12 (motif, :20 dans la plage, daypart trop court, soir sans pubs, 30 min, règles, secours, pas de 4e file).

### Livrable

Recette cochée. Ensuite seulement on reprend [`automate-plan.md`](automate-plan.md) si besoin.

---

## Hors cahier moteur

Ce que le pupitre a rendu visible, et qu’on **n’ajoute pas** à [`automate-horloges.md`](automate-horloges.md) :

- Pads, chaîne régie, MIDI = **overlay live-assist**, pas un 5e *quoi* d’horloge.
- Harbor / live / relais : [`automate-plan.md`](automate-plan.md), en pause.
- Audio, 4e file LS, SPA prod : inchangé.

---

## Si le temps manque

Tenir **0 → 1 → 4** (motif seul, sans pubs calées) n’est **pas** ce cahier : les pubs à heure fixe sont **phase 2**. Découper éventuellement : 0–1 en démo, 2–5 avant d’annoncer l’automate « comme une radio ».

Sans phase 0 signée : **pas de code moteur**. Le proto console peut continuer.
