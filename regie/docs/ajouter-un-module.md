# Ajouter un module

Un module = un dossier `src/regie/modules/<nom>/` avec `setup(kernel)`, une migration SQL, un routeur, une page côté interface. Il ne touche jamais au noyau et n’importe jamais un autre module : les relations passent par `kernel.links`, les réactions par `kernel.outbox.on(...)`, les traitements longs par `kernel.jobs`.

## 1. La migration

`migrations/00NN_<nom>.sql` : tables avec `org_id uuid NOT NULL REFERENCES orgs(id)`, `created_at`, `updated_at` (trigger `regie_touch_updated_at`). Jamais de modification d’une migration déjà déployée : on en ajoute une autre.

## 2. Le service

```python
class MonService:
    name = "mon_module"

    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel
        self.table = Table(kernel.dsn, kernel.org_id, "ma_table", COLUMNS)   # CRUD générique
        kernel.registry.register(EntityKind(kind="chose", module="mon_module", label="Chose", icon="dot",
                                            fetch=self.table.many, summarize=lambda r: {"title": r["name"], "subtitle": ""},
                                            url=lambda i: f"/mon-module/{i}"))
        kernel.actions.register(ActionSpec(kind="mon_module.valider", module="mon_module", entity_kind="chose",
                                           label="Validé", apply=self._apply, revert=self._revert, snapshot=self._snapshot))
        kernel.jobs.register("mon_module.rafraichir", self.job_refresh, "maintenance")
        kernel.scheduler.ensure("mon_module.rafraichir", 900)
        kernel.outbox.on("mail.received", self._on_mail)
```

- `EntityKind` rend le type visible partout : recherche, liens, puces, palette ⌘K.
- `ActionSpec` rend l’action annulable et journalisée ; `lane=` la rend asynchrone (P0).
- `kernel.search.index(kind, id, titre, sous-titre, corps, url)` à chaque écriture.
- `kernel.outbox.emit("chose.created", {...})` pour que les autres réagissent sans couplage.

## 3. Le routeur

`api.py` : `APIRouter(prefix="/api/v1/mon-module")`, dépendances `require("mon_module", "read" | "write")`. Erreurs via `Problem(status, message)`. Ajouter le nom du module dans `kernel/auth.py::MODULES` pour qu’il apparaisse dans la matrice des droits.

## 4. `__init__.py`

```python
def setup(kernel):
    service = MonService(kernel)
    service.router = build_router(service)
    return service
```

Puis l’ajouter à `modules/__init__.py::ALL`.

## 5. L’interface

Une page dans `ui/src/pages/<nom>/`, une route dans `App.tsx`, une entrée dans la navigation (`layout/Shell.tsx`, filtrée par `reg.can(module)`), la route du type dans `lib/registry.tsx::ROUTES`. Utiliser `components/Page.tsx` (Page, Card, Field, Tabs, Pill) et `api/client.ts`.

## 6. Les tests

Un fichier `tests/test_<nom>.py` avec la fixture `app_client` (application complète, admin connecté, jobs exécutés par `kernel.jobs.drain()`). Tester l’action et son annulation, la permission `invite`, et le lien avec au moins un autre type.

## 7. Documentation

Un ADR si le module introduit un choix structurant ; une ligne dans le registre des traitements si des données personnelles apparaissent.
