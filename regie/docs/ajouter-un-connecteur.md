# Ajouter un connecteur

Un connecteur est la seule porte vers un système externe. Il implémente une interface courte, il est enveloppé par le coupe-circuit du noyau, il a un faux jumeau, et il n’écrit jamais deux fois la même chose grâce à `external_refs`.

## Interface (`kernel/connectors.py`)

```python
class MonConnecteur(Connector):
    system = "monsysteme"          # clé unique : connector_state, external_refs
    label = "Mon système"
    read_only = False

    def configured(self) -> bool: ...                    # variables présentes ?
    def pull(self, cursor: dict) -> PullResult: ...      # changements depuis le curseur
    def push(self, entity_kind, entity_id, data, external_id) -> tuple[str, str]: ...  # (external_id, etag)
    def delete(self, external_id) -> None: ...
    def health(self) -> dict: ...
```

## Règles

1. **Idempotence** : avant d’écrire, lire `kernel.connectors.refs.get(system, kind, id)` ; après, `refs.set(...)`. Un rejeu ne crée pas de doublon.
2. **Coupe-circuit** : toujours appeler via `kernel.connectors.guarded(system, fn, ...)` ; 5 échecs d’affilée = pause de 10 minutes, visible dans « État du système », réactivable d’un clic.
3. **Curseur** : `kernel.connectors.state.cursor(system)` / `state.ok(system, cursor=...)` ; jamais d’état dans le processus.
4. **Mappage défensif** : une fonction pure `map_x(raw) -> (row, warnings)`, testée sur un échantillon figé ; champ inconnu ignoré et signalé, jamais bloquant.
5. **Secrets** : jetons par compte dans `kernel.secrets` (AES-GCM), variables globales dans `secrets.env`. Jamais de secret dans un message d’erreur ni un log.
6. **Faux jumeau** : `FakeX` avec la même surface, en mémoire, qui reproduit les cas limites (410 syncToken expiré, 412 etag, panne). Les tests du module l’injectent via `setup(kernel, connector=FakeX())`.
7. **Job** : la lecture périodique est un job (`kernel.jobs.register(..., lane="monsysteme")`) planifié par `kernel.scheduler.ensure(...)`, jamais un thread maison.

## Exemples

- Lecture seule : `connectors/outlive.py` (PostgREST, cache, liens automatiques).
- Bidirectionnel : `connectors/google.py` + `modules/calendar/service.py` (syncToken, conflits conservés, garde-fou de 20 écritures).
- Écriture idempotente : `connectors/wordpress.py` (`external_refs` → même article mis à jour).
- Dépôt statique : `connectors/edge.py` (Vercel, Cloudflare Pages, dossier).
