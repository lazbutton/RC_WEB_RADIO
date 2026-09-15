# Player public — New Trad Radio

App Vite + React : embed d’écoute (titre, play, oscilloscope live). Charte newtradfest.com. Cadre visé ~700×200 px.

Radiotomate n’expose pas de page d’écoute grand public. Ce dossier **est** le site auditeur.

## Embed

À coller sur newtradfest.com (ou autre vhost autorisé par `frame-ancestors` dans [`ops/Caddyfile`](../ops/Caddyfile)) :

```html
<iframe
  src="https://ntradio.example.org/"
  title="New Trad Radio"
  style="width:100%;height:200px;border:0;border-radius:12px"
  allow="autoplay"
></iframe>
```

## Lancer

```bash
cd player
npm install
npm run dev
# http://127.0.0.1:5174/  (port 5174 pour ne pas coller un autre Vite sur 5173)
```

Prod :

```bash
npm run build
# copier player/dist/ vers /var/www/ntradio-public
```

Dans `public/config.js` (copié tel quel dans `dist/`), mettre `streamUrl` = `productionStreamUrl` au déploiement.

Icecast + nowplaying : [`ops/banc-mac.md`](../ops/banc-mac.md). Sans source Icecast, le player est muet.

## Config runtime (`public/config.js`)

Injectable sans rebuild (`window.NTR_CONFIG`) :

| Clé | Banc | Prod |
|---|---|---|
| `streamUrl` | `http://127.0.0.1:18000/ntradio.mp3` | URL Labomedia ou `/ntradio.mp3` (Caddy) |
| `productionStreamUrl` | Labomedia | — |
| `nowUrl` | `http://127.0.0.1:6820/now.json` | sidecar / `relay_to` |
| `icecastStatusUrl` | Icecast `status-json.xsl` | idem |
| `festivalUrl` | `https://newtradfest.com/` | idem |

## Oscilloscope

Le MP3 est d’abord lu en **fetch CORS** + `MediaSource` (blob same-origin), puis `MediaElementSource` → `AnalyserNode`. C’est ce qui fait bouger l’onde : un `<audio src="http://icecast:…">` cross-origin laisse l’analyseur plat.

Banc : Icecast envoie `Access-Control-Allow-Origin: *` ([`ops/local/icecast.xml`](../ops/local/icecast.xml)).

Prod : CORS Icecast **ou** flux same-origin `streamUrl: "/ntradio.mp3"` + `handle /ntradio.mp3` dans [`ops/Caddyfile`](../ops/Caddyfile).

Si MSE/CORS échoue, la lecture directe continue ; le canvas reste une ligne.

## Fichiers

| Chemin | Rôle |
|---|---|
| `src/App.tsx` | Layout embed (titre + play + onde) |
| `src/useRadio.ts` | Flux, graphe Web Audio, Now Playing |
| `src/Oscilloscope.tsx` | Canvas trigger |
| `src/PlayButton.tsx` | Morph play/stop (Motion) |
| `public/config.js` | URLs |
| `public/logo-ntr*` | Marque |
