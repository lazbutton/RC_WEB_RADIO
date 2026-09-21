-- Phase 3 : émissions, épisodes (masters NAS), séquences (bornes), podcasts, publications.

CREATE TABLE shows (
  id serial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  slug text NOT NULL,
  name text NOT NULL,
  description text NOT NULL DEFAULT '',
  folder text NOT NULL DEFAULT '',
  schedule text NOT NULL DEFAULT '',
  duration_min integer NOT NULL DEFAULT 60,
  hosts uuid[] NOT NULL DEFAULT '{}',
  color text NOT NULL DEFAULT '#111',
  handles jsonb NOT NULL DEFAULT '{"in": 0.5, "out": 0.8}'::jsonb,
  rss_enabled boolean NOT NULL DEFAULT true,
  cover_path text NOT NULL DEFAULT '',
  tags text[] NOT NULL DEFAULT '{}',
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, slug)
);
CREATE TRIGGER shows_touch BEFORE UPDATE ON shows FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();

CREATE TABLE episodes (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  show_id integer NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
  title text NOT NULL DEFAULT '',
  aired_on date,
  master_path text NOT NULL,
  duration_s double precision NOT NULL DEFAULT 0,
  sample_rate integer NOT NULL DEFAULT 0,
  channels integer NOT NULL DEFAULT 0,
  loudness_i double precision,
  waveform jsonb NOT NULL DEFAULT '[]'::jsonb,
  transcript_path text NOT NULL DEFAULT '',
  transcript_status text NOT NULL DEFAULT 'none' CHECK (transcript_status IN ('none', 'queued', 'running', 'done', 'failed')),
  transcript_error text NOT NULL DEFAULT '',
  bornes_path text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'detected' CHECK (status IN ('detected', 'transcribed', 'bounded', 'edited', 'published')),
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, master_path)
);
CREATE INDEX episodes_show_idx ON episodes (show_id, aired_on DESC);
CREATE TRIGGER episodes_touch BEFORE UPDATE ON episodes FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();

CREATE TABLE segments (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  episode_id bigint NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  position integer NOT NULL DEFAULT 0,
  title text NOT NULL DEFAULT '',
  kind text NOT NULL DEFAULT 'itw' CHECK (kind IN ('itw', 'chro', 'live', 'debat', 'plateau', 'autre')),
  start_s double precision NOT NULL,
  end_s double precision NOT NULL,
  phrase_in text NOT NULL DEFAULT '',
  phrase_out text NOT NULL DEFAULT '',
  speaker text NOT NULL DEFAULT '',
  coupures jsonb NOT NULL DEFAULT '[]'::jsonb,
  handles jsonb,
  valid boolean NOT NULL DEFAULT false,
  confidence double precision NOT NULL DEFAULT 0,
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (end_s > start_s)
);
CREATE INDEX segments_episode_idx ON segments (episode_id, position);
CREATE TRIGGER segments_touch BEFORE UPDATE ON segments FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();

CREATE TABLE podcasts (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  show_id integer NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
  episode_id bigint REFERENCES episodes(id) ON DELETE SET NULL,
  segment_id bigint REFERENCES segments(id) ON DELETE SET NULL,
  slug text NOT NULL,
  title text NOT NULL,
  description text NOT NULL DEFAULT '',
  file_path text NOT NULL DEFAULT '',
  file_size bigint NOT NULL DEFAULT 0,
  duration_s double precision NOT NULL DEFAULT 0,
  loudness_i double precision,
  cover_path text NOT NULL DEFAULT '',
  rights_status text NOT NULL DEFAULT 'unknown' CHECK (rights_status IN ('unknown', 'ok', 'blocked')),
  rights_notes text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'exporting', 'exported', 'published', 'failed')),
  export_error text NOT NULL DEFAULT '',
  published_at timestamptz,
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, slug)
);
CREATE INDEX podcasts_show_idx ON podcasts (show_id, published_at DESC);
CREATE TRIGGER podcasts_touch BEFORE UPDATE ON podcasts FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();

CREATE TABLE publications (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  entity_kind text NOT NULL,
  entity_id text NOT NULL,
  target text NOT NULL,
  external_id text NOT NULL DEFAULT '',
  url text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed', 'removed')),
  error text NOT NULL DEFAULT '',
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  done_at timestamptz,
  UNIQUE (entity_kind, entity_id, target)
);
