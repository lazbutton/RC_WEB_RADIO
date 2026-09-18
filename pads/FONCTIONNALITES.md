# Pads — fonctionnalités radio

Cartwall studio : grille 8×8, lecture Web Audio, library locale **et** catalogue Nasgul, APC Mini MK2.

Ce fichier suit l’état du code. Trois colonnes d’avancement : **fait**, **en cours**, **à faire**.

---

## Position

Un animateur lance jingles, pubs, stings et beds **en local**, vers la carte son du navigateur (Chrome, Edge ou Safari, SysEx pour les LED).  
Mapping et blobs restent dans le browser (`localStorage` + IndexedDB). C’est la surface live, pas l’automate.

---

## Fait

Livré et branché dans l’UI.

| Quoi | Pourquoi radio |
|---|---|
| Grille 8×8 (notes MIDI 0–63, bas-gauche = 0) | Un coup d’œil = 64 cases, comme une APC |
| Mapping sauvé (`pads-map-v1`) | La grille survit au refresh |
| Catégories éditables (IndexedDB v3) | Music / Jingles / Pubs en seed, puis autant qu’on veut |
| Créer / renommer / recolorer une catégorie | Habillage par émission (ID, liners, pubs…) |
| Supprimer une catégorie vide | Ranger sans casser un pad encore mappé |
| Import fichiers audio (mp3, wav, flac, ogg, m4a, aiff…) | Library locale, hors réseau |
| Catalogue Nasgul (recherche HTTP + cache des pads mappés) | Même banque que Radiotomate / Nextcloud |
| Import **dossier** (bouton + drop OS) | Un pad = un carton aléatoire |
| Pad dossier : tirage aléatoire, jamais le même deux fois de suite | Rotation jingles / pubs sans doublon immédiat |
| Drag son ou dossier → pad ; swap pads ; drop hors grille = vider | Mapper vite avant l’antenne |
| Drop fichiers sur une case (plusieurs = dossier) | Remplir la grille sans passer par la rail |
| Drop sur pad : dernière catégorie d’import, sinon la première | Pas de kind forcé « son » |
| Clic ou pad physique = play | Fire immédiat |
| Clic droit = couleur pad (12 teintes, écran + LED) | Code couleur régie |
| Stop global (scène 1 / note 112) | Couper tout d’un coup |
| « Jingle après » (scène 2 / note 113) **arme** les pads `jingle` | Rappel visuel après un son |
| LED occupé coloré · lecture blanc clignotant (plus vite ≤ 5 s) · jingles armés en pulse | Lire la grille sans regarder l’écran |
| Now + temps restant (dernière voix) | Compte à rebours on-air |
| Modes Live / Régler (`pads-ui-mode`) | Live = fire only ; Régler = mapper |
| Library tiroir en Live (`Sons`, `/`, Esc) | Grille pleine largeur à l’antenne |
| Bandeau **En l’air** / **Silence**, −mm:ss, horloge `fr-FR` | Lisibilité à 2 m |
| Barre de progression sur le pad on-air | Où on en est sans quitter la case |
| Recherche + onglets catégories | Un geste pour trouver un son |
| Plein écran | Pupitre, pas une fenêtre d’appli |
| Labels UI 100 % FR | `En l’air`, `Silence`, `Bibliothèque`, `Régler` |
| Unlock audio au premier clic | Contourne le blocage autoplay |
| Delete son (nettoie folders + map) ; delete dossier (blobs compris) | Pas de pad fantôme |
| MIDI : connexion, reconnexion, SysEx, debounce 90 ms | APC fiable en live |

Seed catégories : `son` → Music (ambre), `jingle` → Jingles (rouge), `pub` → Pubs (orange). Les **ids** restent `son` / `jingle` / `pub` même si on renomme le titre.

---

## En cours

Existe, mais pas encore calé pour une régie.

| Quoi | État | Manque radio |
|---|---|---|
| Chaîne « Jingle après » | Arme UI + LED seulement | N’enchaîne **pas** le jingle tout seul |
| Chaîne liée aux ids seed | `kind === "son"` arme, `kind === "jingle"` désarme | Une nouvelle catégorie « Jingles » **ne compte pas** |
| Recolor catégorie | Pastille + menu | Ne se propage **pas** aux sons / dossiers / pads déjà mappés |
| Now playing | Dernière voix seulement | Les autres sons restent audibles **sans** compteur |
| Overlap | Chaque fire empile une voix | Pas de stop **ce** pad, pas de restart / exclusif |
| Dossier | Random only | Pas de mode séquentiel (pubs à tour de rôle) |
| Durée pad dossier | Nombre de fichiers | Durée du prochain tirage inconnue avant play |
| Drop sur pad vide | Dernière catégorie utilisée | Pas d’inférence jingle / pub depuis le dossier |
| APC Shift (122) | Ignoré | Pas de modificateur cue / stop |
| Faders APC | Muets | Pas de trim / master physique |
| Track buttons 100–107 | Éteints | Pas de pages / banques |
| Scènes 114–119 | Inutilisées | Panic, lock, page, cue encore à mapper |

---

## À faire — calage radio

Priorité : ce qu’on touche **pendant** l’émission d’abord.

### P0 — Live-safe

| Fonction | Pourquoi |
|---|---|
| Stop **ce** pad (long-press ou Shift+pad) | Couper un jingle sans tuer le bed |
| Policy de fire par pad : restart / layer / exclusif / ignorer | Un 2ᵉ coup = recommencer, pas empiler |
| Fade-out court au stop | Coupe propre, pas de clic |
| Panic silence (scène libre) | Tout meurt **maintenant** |
| Polyphonie max + mute des voix orphelines | Éviter 8 jingles empilés |

### P1 — Cartwall métier

| Fonction | Pourquoi |
|---|---|
| Pages / maps 8×8 (matinale, live, pubs) | Une grille par émission |
| Track buttons APC = changer de page | Sans quitter les pads |
| Chaîne réelle son → jingle (auto-play à la fin) | Habillage sans 2ᵉ geste |
| Rôle « musique / jingle / pub » **par catégorie**, pas par id seed | « Jingle après » suit le métier, pas `jingle` |
| Dossier **séquentiel** (en plus du random) | Pubs, IDs, liners dans l’ordre |
| Recolor catégorie **en cascade** (library + pads) | Une teinte = une banque |
| Cue casque vs PGM (Shift+pad = préécoute) | Checker sans envoyer à la table |
| Volume / trim par pad + faders APC | Niveau jingle ≠ bed |
| Scènes 3–8 : panic, lock, page ±, cue, chain | Tout le bord de l’APC sert |
| Import / export mapping + library (JSON + fichiers) | Preset émission, backup, autre machine |

### P2 — Studio

| Fonction | Pourquoi |
|---|---|
| Fade in/out paramétrable | Beds, pubs |
| Ducking local (jingle au-dessus d’un bed) | Parole / musique sous un sting |
| Device de sortie (carte son studio) | PGM ≠ casque OS |
| Limiteur léger / true peak | Protéger le bus |
| Bed en boucle (latch) | Fond sous chronique |
| Start offset / end cue | Taille un jingle trop long |
| Rename / tags des sons | Trouver « soupe » en 2 s |
| Déjà passé aujourd’hui / anti-répétition journée | Pas le même ID toutes les 10 min |
| Quota IndexedDB + backup | Library qui grossit |
| Journal local des fires (heure, titre, pad) | Log d’émission |
| Raccourcis clavier | Sans APC |
| MIDI learn (autre surface) | Stream Deck, 2ᵉ contrôleur |
| Meter de sortie | Voir si ça sort |

### Matériel APC encore libre

| Contrôle | Note / CC | Usage proposé |
|---|---|---|
| Shift | 122 | Cue ou stop ce pad |
| Track 1–8 | 100–107 | Pages / banques |
| Scènes 3–8 | 114–119 | Panic, lock, page ±, cue |
| 8 faders + master | CC | Trim colonnes / master |

---

## Hors scope

- Radiotomate, console `/antenne`, files Liquidsoap, Icecast, auth
- Devenir l’automate ou une 4ᵉ file de playout
- Ducking / crossfade **côté serveur** (ici : mix local seulement)

Pads = overlay live-assist **dans le browser**, pas le moteur d’antenne.
