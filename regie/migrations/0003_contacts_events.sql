-- Phase 1 : contacts (personnes, structures, lieux, affiliations, interactions) et événements Outlive (cache + couverture).

CREATE TABLE people (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  display_name text NOT NULL,
  first_name text NOT NULL DEFAULT '',
  last_name text NOT NULL DEFAULT '',
  emails jsonb NOT NULL DEFAULT '[]'::jsonb,
  phones jsonb NOT NULL DEFAULT '[]'::jsonb,
  job_title text NOT NULL DEFAULT '',
  notes text NOT NULL DEFAULT '',
  tags text[] NOT NULL DEFAULT '{}',
  source text NOT NULL DEFAULT 'manual',
  carddav_uid text NOT NULL DEFAULT '',
  outlive_artist_id text NOT NULL DEFAULT '',
  merged_into bigint REFERENCES people(id),
  retention_until date,
  name_key text GENERATED ALWAYS AS (lower(regie_unaccent(display_name))) STORED,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX people_name_key_idx ON people (org_id, name_key);
CREATE INDEX people_emails_idx ON people USING gin (emails);
CREATE INDEX people_name_trgm_idx ON people USING gin (name_key gin_trgm_ops);
CREATE TRIGGER people_touch BEFORE UPDATE ON people FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();

CREATE TABLE organizations (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  name text NOT NULL,
  kind text NOT NULL DEFAULT 'autre',
  website text NOT NULL DEFAULT '',
  emails jsonb NOT NULL DEFAULT '[]'::jsonb,
  phones jsonb NOT NULL DEFAULT '[]'::jsonb,
  domains text[] NOT NULL DEFAULT '{}',
  address text NOT NULL DEFAULT '',
  notes text NOT NULL DEFAULT '',
  tags text[] NOT NULL DEFAULT '{}',
  outlive_organizer_id text NOT NULL DEFAULT '',
  merged_into bigint REFERENCES organizations(id),
  name_key text GENERATED ALWAYS AS (lower(regie_unaccent(name))) STORED,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX organizations_name_key_idx ON organizations (org_id, name_key);
CREATE INDEX organizations_domains_idx ON organizations USING gin (domains);
CREATE TRIGGER organizations_touch BEFORE UPDATE ON organizations FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();

CREATE TABLE places (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  name text NOT NULL,
  address text NOT NULL DEFAULT '',
  city text NOT NULL DEFAULT '',
  lat double precision,
  lng double precision,
  capacity integer,
  website text NOT NULL DEFAULT '',
  notes text NOT NULL DEFAULT '',
  outlive_location_id text NOT NULL DEFAULT '',
  merged_into bigint REFERENCES places(id),
  name_key text GENERATED ALWAYS AS (lower(regie_unaccent(name))) STORED,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX places_name_key_idx ON places (org_id, name_key);
CREATE TRIGGER places_touch BEFORE UPDATE ON places FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();

CREATE TABLE affiliations (
  id bigserial PRIMARY KEY,
  person_id bigint NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  organization_id bigint NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  role text NOT NULL DEFAULT '',
  since date,
  until date,
  UNIQUE (person_id, organization_id, role)
);

CREATE TABLE interactions (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  kind text NOT NULL DEFAULT 'note' CHECK (kind IN ('mail', 'call', 'meeting', 'note', 'visit')),
  person_id bigint REFERENCES people(id) ON DELETE CASCADE,
  organization_id bigint REFERENCES organizations(id) ON DELETE CASCADE,
  at timestamptz NOT NULL DEFAULT now(),
  summary text NOT NULL DEFAULT '',
  ref_kind text NOT NULL DEFAULT '',
  ref_id text NOT NULL DEFAULT '',
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX interactions_person_idx ON interactions (person_id, at DESC);
CREATE INDEX interactions_org_idx ON interactions (organization_id, at DESC);

-- Cache des événements Outlive : la vérité reste chez Outlive, on garde le brut et une projection.
CREATE TABLE outlive_events (
  id text PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  title text NOT NULL,
  description text NOT NULL DEFAULT '',
  starts_at timestamptz NOT NULL,
  ends_at timestamptz,
  category text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'approved',
  city text NOT NULL DEFAULT '',
  address text NOT NULL DEFAULT '',
  venue_id text NOT NULL DEFAULT '',
  venue_name text NOT NULL DEFAULT '',
  organizer_id text NOT NULL DEFAULT '',
  organizer_name text NOT NULL DEFAULT '',
  artists jsonb NOT NULL DEFAULT '[]'::jsonb,
  price text NOT NULL DEFAULT '',
  url text NOT NULL DEFAULT '',
  image_url text NOT NULL DEFAULT '',
  agenda_types text[] NOT NULL DEFAULT '{}',
  is_radio_campus boolean NOT NULL DEFAULT false,
  raw jsonb NOT NULL DEFAULT '{}'::jsonb,
  warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
  source_updated_at timestamptz,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  gone_at timestamptz
);
CREATE INDEX outlive_events_starts_idx ON outlive_events (org_id, starts_at);
CREATE INDEX outlive_events_organizer_idx ON outlive_events (organizer_id);

-- Couverture éditoriale d'un événement (annonce, interview, direct, chronique).
CREATE TABLE coverage (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  event_id text NOT NULL REFERENCES outlive_events(id) ON DELETE CASCADE,
  kind text NOT NULL DEFAULT 'annonce' CHECK (kind IN ('annonce', 'interview', 'direct', 'chronique', 'reportage', 'partenariat')),
  status text NOT NULL DEFAULT 'idea' CHECK (status IN ('idea', 'planned', 'done', 'skipped')),
  assignee_id uuid REFERENCES users(id),
  notes text NOT NULL DEFAULT '',
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (event_id, kind)
);
CREATE TRIGGER coverage_touch BEFORE UPDATE ON coverage FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();
