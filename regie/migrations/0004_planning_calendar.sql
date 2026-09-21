-- Phase 2 : tâches, commentaires, agendas Google (comptes, événements, historique), disponibilités.

CREATE TABLE tasks (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  title text NOT NULL,
  description text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'doing', 'done', 'cancelled')),
  priority integer NOT NULL DEFAULT 1,
  due_at timestamptz,
  start_at timestamptz,
  all_day boolean NOT NULL DEFAULT true,
  assignees uuid[] NOT NULL DEFAULT '{}',
  checklist jsonb NOT NULL DEFAULT '[]'::jsonb,
  tags text[] NOT NULL DEFAULT '{}',
  position integer NOT NULL DEFAULT 0,
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  done_at timestamptz
);
CREATE INDEX tasks_status_idx ON tasks (org_id, status, position);
CREATE INDEX tasks_due_idx ON tasks (org_id, due_at);
CREATE INDEX tasks_assignees_idx ON tasks USING gin (assignees);
CREATE TRIGGER tasks_touch BEFORE UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();

CREATE TABLE comments (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  entity_kind text NOT NULL,
  entity_id text NOT NULL,
  author_id uuid REFERENCES users(id),
  body text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  edited_at timestamptz
);
CREATE INDEX comments_entity_idx ON comments (entity_kind, entity_id, created_at);

CREATE TABLE calendar_accounts (
  id serial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  user_id uuid REFERENCES users(id),
  provider text NOT NULL DEFAULT 'google' CHECK (provider IN ('google', 'ics')),
  email text NOT NULL DEFAULT '',
  calendar_id text NOT NULL DEFAULT 'primary',
  label text NOT NULL DEFAULT '',
  color text NOT NULL DEFAULT '#111',
  shared boolean NOT NULL DEFAULT false,
  ics_url text NOT NULL DEFAULT '',
  sync_token text NOT NULL DEFAULT '',
  enabled boolean NOT NULL DEFAULT true,
  status text NOT NULL DEFAULT 'idle',
  error text NOT NULL DEFAULT '',
  pending_bulk integer NOT NULL DEFAULT 0,
  last_sync_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE calendar_events (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  account_id integer NOT NULL REFERENCES calendar_accounts(id) ON DELETE CASCADE,
  external_id text NOT NULL DEFAULT '',
  title text NOT NULL DEFAULT '',
  description text NOT NULL DEFAULT '',
  location text NOT NULL DEFAULT '',
  starts_at timestamptz NOT NULL,
  ends_at timestamptz NOT NULL,
  all_day boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'confirmed',
  attendees jsonb NOT NULL DEFAULT '[]'::jsonb,
  html_link text NOT NULL DEFAULT '',
  etag text NOT NULL DEFAULT '',
  remote_updated_at timestamptz,
  local_updated_at timestamptz NOT NULL DEFAULT now(),
  dirty boolean NOT NULL DEFAULT false,
  history jsonb NOT NULL DEFAULT '[]'::jsonb,
  source text NOT NULL DEFAULT 'google',
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz
);
CREATE UNIQUE INDEX calendar_events_external_idx ON calendar_events (account_id, external_id) WHERE external_id <> '';
CREATE INDEX calendar_events_range_idx ON calendar_events (org_id, starts_at, ends_at);
CREATE INDEX calendar_events_dirty_idx ON calendar_events (account_id) WHERE dirty;

CREATE TABLE availability (
  id serial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  weekday integer NOT NULL CHECK (weekday BETWEEN 0 AND 6),
  start_time time NOT NULL,
  end_time time NOT NULL,
  note text NOT NULL DEFAULT ''
);

CREATE TABLE absences (
  id serial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  start_date date NOT NULL,
  end_date date NOT NULL,
  reason text NOT NULL DEFAULT ''
);
