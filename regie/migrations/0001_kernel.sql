-- Noyau Régie : comptes, registre, liens, actions, jobs, outbox, connecteurs, recherche, notifications, fichiers.
-- Toutes les tables portent org_id : une organisation au départ, plusieurs possibles sans refonte.

CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- unaccent() est STABLE ; une colonne générée exige IMMUTABLE.
CREATE OR REPLACE FUNCTION regie_unaccent(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $$
  SELECT public.unaccent('public.unaccent', $1)
$$;

CREATE OR REPLACE FUNCTION regie_touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END $$;

CREATE TABLE orgs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text NOT NULL UNIQUE,
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES orgs(id),
  email text NOT NULL,
  name text NOT NULL DEFAULT '',
  password_hash text NOT NULL DEFAULT '',
  role text NOT NULL DEFAULT 'membre' CHECK (role IN ('admin', 'membre', 'invite')),
  prefs jsonb NOT NULL DEFAULT '{}'::jsonb,
  disabled_at timestamptz,
  last_login_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email_idx ON users (org_id, lower(email));
CREATE TRIGGER users_touch BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();

CREATE TABLE sessions (
  token_hash text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  ip text NOT NULL DEFAULT '',
  user_agent text NOT NULL DEFAULT ''
);
CREATE INDEX sessions_user_idx ON sessions (user_id);

CREATE TABLE auth_events (
  id bigserial PRIMARY KEY,
  org_id uuid REFERENCES orgs(id),
  user_id uuid,
  kind text NOT NULL,
  ip text NOT NULL DEFAULT '',
  detail text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX auth_events_created_idx ON auth_events (created_at);

-- Permissions par rôle et par module : none | read | write | admin.
CREATE TABLE permissions (
  org_id uuid NOT NULL REFERENCES orgs(id),
  role text NOT NULL,
  module text NOT NULL,
  level text NOT NULL CHECK (level IN ('none', 'read', 'write', 'admin')),
  PRIMARY KEY (org_id, role, module)
);

CREATE TABLE settings (
  org_id uuid NOT NULL REFERENCES orgs(id),
  key text NOT NULL,
  value text NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, key)
);

-- Secrets chiffrés (jetons OAuth, clés) : la clé de chiffrement vit dans REGIE_SECRET, jamais en base.
CREATE TABLE secrets (
  org_id uuid NOT NULL REFERENCES orgs(id),
  scope text NOT NULL,
  key text NOT NULL,
  ciphertext bytea NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, scope, key)
);

-- Registre d'entités : miroir en base du registre déclaré dans le code (documentation, contraintes souples).
CREATE TABLE entity_kinds (
  kind text PRIMARY KEY,
  module text NOT NULL,
  label text NOT NULL,
  label_plural text NOT NULL DEFAULT '',
  icon text NOT NULL DEFAULT '',
  table_name text NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Liens polymorphes : toutes les relations transverses entre modules.
CREATE TABLE links (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  src_kind text NOT NULL,
  src_id text NOT NULL,
  dst_kind text NOT NULL,
  dst_id text NOT NULL,
  role text NOT NULL DEFAULT '',
  meta jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (src_kind, src_id, dst_kind, dst_id, role)
);
CREATE INDEX links_src_idx ON links (src_kind, src_id);
CREATE INDEX links_dst_idx ON links (dst_kind, dst_id);

-- Journal d'actions réversibles (généralisation d'Inbox Zero).
CREATE TABLE actions (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  module text NOT NULL,
  kind text NOT NULL,
  label text NOT NULL DEFAULT '',
  entity_kind text NOT NULL DEFAULT '',
  entity_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  params jsonb NOT NULL DEFAULT '{}'::jsonb,
  before jsonb NOT NULL DEFAULT '{}'::jsonb,
  after jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed', 'undone')),
  error text NOT NULL DEFAULT '',
  actor_id uuid,
  undo_of bigint,
  job_id bigint,
  created_at timestamptz NOT NULL DEFAULT now(),
  done_at timestamptz,
  undone_at timestamptz
);
CREATE INDEX actions_created_idx ON actions (org_id, created_at DESC);
CREATE INDEX actions_entity_idx ON actions USING gin (entity_ids);

-- Jobs persistants : bail, retentatives, idempotence, plusieurs travailleurs.
CREATE TABLE jobs (
  id bigserial PRIMARY KEY,
  org_id uuid REFERENCES orgs(id),
  lane text NOT NULL,
  kind text NOT NULL,
  priority integer NOT NULL DEFAULT 2,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key text,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed', 'dead', 'cancelled')),
  attempts integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 5,
  run_at timestamptz NOT NULL DEFAULT now(),
  lease_until timestamptz,
  worker text NOT NULL DEFAULT '',
  progress text NOT NULL DEFAULT '',
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text NOT NULL DEFAULT '',
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz
);
CREATE UNIQUE INDEX jobs_idempotency_idx ON jobs (idempotency_key) WHERE idempotency_key IS NOT NULL AND status IN ('queued', 'running');
CREATE INDEX jobs_claim_idx ON jobs (lane, status, run_at, priority) WHERE status IN ('queued', 'running');
CREATE INDEX jobs_created_idx ON jobs (created_at DESC);

CREATE TABLE schedules (
  id serial PRIMARY KEY,
  org_id uuid REFERENCES orgs(id),
  kind text NOT NULL,
  lane text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  every_seconds integer NOT NULL,
  priority integer NOT NULL DEFAULT 3,
  enabled boolean NOT NULL DEFAULT true,
  next_run_at timestamptz NOT NULL DEFAULT now(),
  last_run_at timestamptz,
  UNIQUE (kind, payload)
);

-- Outbox transactionnelle : un événement métier est écrit dans la même transaction que la donnée.
CREATE TABLE outbox (
  id bigserial PRIMARY KEY,
  org_id uuid REFERENCES orgs(id),
  topic text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  delivered_at timestamptz,
  attempts integer NOT NULL DEFAULT 0,
  error text NOT NULL DEFAULT ''
);
CREATE INDEX outbox_pending_idx ON outbox (id) WHERE delivered_at IS NULL;

-- Références externes : ce que chaque système connaît de nos entités (idempotence des écritures).
CREATE TABLE external_refs (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  system text NOT NULL,
  entity_kind text NOT NULL,
  entity_id text NOT NULL,
  external_id text NOT NULL,
  etag text NOT NULL DEFAULT '',
  version bigint NOT NULL DEFAULT 0,
  meta jsonb NOT NULL DEFAULT '{}'::jsonb,
  synced_at timestamptz,
  last_error text NOT NULL DEFAULT '',
  UNIQUE (system, entity_kind, entity_id),
  UNIQUE (system, entity_kind, external_id)
);

-- État des connecteurs : curseur, coupe-circuit, santé.
CREATE TABLE connector_state (
  system text PRIMARY KEY,
  org_id uuid REFERENCES orgs(id),
  cursor jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'idle' CHECK (status IN ('idle', 'ok', 'error', 'paused', 'disabled')),
  failures integer NOT NULL DEFAULT 0,
  paused_until timestamptz,
  last_ok_at timestamptz,
  last_error text NOT NULL DEFAULT '',
  meta jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Recherche unifiée.
CREATE TABLE search_documents (
  kind text NOT NULL,
  id text NOT NULL,
  org_id uuid REFERENCES orgs(id),
  title text NOT NULL DEFAULT '',
  subtitle text NOT NULL DEFAULT '',
  body text NOT NULL DEFAULT '',
  url text NOT NULL DEFAULT '',
  meta jsonb NOT NULL DEFAULT '{}'::jsonb,
  tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', regie_unaccent(coalesce(title, ''))), 'A') ||
    setweight(to_tsvector('simple', regie_unaccent(coalesce(subtitle, ''))), 'B') ||
    setweight(to_tsvector('simple', regie_unaccent(coalesce(body, ''))), 'C')
  ) STORED,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (kind, id)
);
CREATE INDEX search_documents_tsv_idx ON search_documents USING gin (tsv);
CREATE INDEX search_documents_title_trgm_idx ON search_documents USING gin (regie_unaccent(title) gin_trgm_ops);
CREATE INDEX search_documents_updated_idx ON search_documents (updated_at DESC);

-- Notifications in-app et Web Push.
CREATE TABLE notifications (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind text NOT NULL,
  text text NOT NULL,
  entity_kind text NOT NULL DEFAULT '',
  entity_id text NOT NULL DEFAULT '',
  url text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  read_at timestamptz
);
CREATE INDEX notifications_user_idx ON notifications (user_id, read_at, created_at DESC);

CREATE TABLE push_subscriptions (
  id serial PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  endpoint text NOT NULL UNIQUE,
  keys jsonb NOT NULL DEFAULT '{}'::jsonb,
  user_agent text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Fichiers du NAS : index seulement, les octets restent sur le dataset.
CREATE TABLE files (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  path text NOT NULL UNIQUE,
  name text NOT NULL,
  size bigint NOT NULL DEFAULT 0,
  mime text NOT NULL DEFAULT '',
  kind text NOT NULL DEFAULT 'other',
  sha256 text NOT NULL DEFAULT '',
  meta jsonb NOT NULL DEFAULT '{}'::jsonb,
  modified_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX files_kind_idx ON files (kind);
CREATE TRIGGER files_touch BEFORE UPDATE ON files FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();

-- Organisation par défaut et permissions de base.
INSERT INTO orgs (slug, name) VALUES ('radio-campus', 'Radio Campus Orléans');
