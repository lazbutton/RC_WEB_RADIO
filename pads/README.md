# Pads

Surface 8×8, APC Mini MK2, lecture Web Audio.  
Bibliothèque **locale** (IndexedDB) et **Nasgul** (catalogue http://192.168.1.100:30127). Les pads mappés sont cachés en local pour tenir si le NAS tousse.

```bash
cd pads
npm install
npm run dev
# http://127.0.0.1:5176/
# optionnel : VITE_CATALOG_URL=http://192.168.1.100:30127
```

Modes **Live** (défaut, grille pleine, library tiroir, pas de drag) et **Régler** (mapping). Clic ou pad physique = play. Clic droit = couleur. En Régler, glisser un son **ou un dossier** vers une case.

- Chrome, Edge ou Safari — autoriser **SysEx** pour les LED
- Sons : banque Nasgul (`:30127`) ou fichiers audio importés (IndexedDB, restent dans le navigateur)

```bash
cd pads
npm install
npm run dev
# http://127.0.0.1:5176/
```

| | |
|---|---|
| Grille | 8×8, mapping sauvé en local |
| Library | Catégories (nom + couleur), fichiers, ou **dossier** aléatoire |
| Clic droit | Couleur du pad (écran + LED APC) |
| Scène 1 (haut droite) | Stop |
| Scène 2 | Jingle après le son |
| LED | Occupé coloré · en lecture blanc clignotant |
