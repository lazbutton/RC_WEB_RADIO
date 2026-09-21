-- Phase 5 : vie de la radio. Nouveaux types déclarés au registre ; le noyau ne change pas.

CREATE TABLE guests (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  person_id bigint REFERENCES people(id) ON DELETE SET NULL,
  name text NOT NULL DEFAULT '',
  topic text NOT NULL DEFAULT '',
  show_id integer REFERENCES shows(id) ON DELETE SET NULL,
  episode_id bigint REFERENCES episodes(id) ON DELETE SET NULL,
  planned_on date,
  status text NOT NULL DEFAULT 'idee' CHECK (status IN ('idee', 'contacte', 'confirme', 'venu', 'annule')),
  authorization_status text NOT NULL DEFAULT 'none' CHECK (authorization_status IN ('none', 'requested', 'signed', 'refused')),
  authorization_signed_at timestamptz,
  authorization_scope text NOT NULL DEFAULT 'antenne, podcast, réseaux sociaux',
  attestation_path text NOT NULL DEFAULT '',
  notes text NOT NULL DEFAULT '',
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX guests_status_idx ON guests (org_id, status);
CREATE TRIGGER guests_touch BEFORE UPDATE ON guests FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();

CREATE TABLE resources (
  id serial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  name text NOT NULL,
  kind text NOT NULL DEFAULT 'studio' CHECK (kind IN ('studio', 'materiel', 'salle', 'vehicule')),
  notes text NOT NULL DEFAULT '',
  active boolean NOT NULL DEFAULT true,
  UNIQUE (org_id, name)
);

CREATE TABLE bookings (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  resource_id integer NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  title text NOT NULL DEFAULT '',
  user_id uuid REFERENCES users(id),
  starts_at timestamptz NOT NULL,
  ends_at timestamptz NOT NULL,
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (ends_at > starts_at)
);
CREATE INDEX bookings_range_idx ON bookings (resource_id, starts_at, ends_at);

CREATE TABLE volunteers (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  person_id bigint NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  kind text NOT NULL DEFAULT 'benevole' CHECK (kind IN ('benevole', 'service_civique', 'stagiaire', 'salarie')),
  mission text NOT NULL DEFAULT '',
  start_date date,
  end_date date,
  hours_per_week numeric(5,1),
  tutor_id uuid REFERENCES users(id),
  status text NOT NULL DEFAULT 'actif' CHECK (status IN ('candidat', 'actif', 'termine')),
  attestation_path text NOT NULL DEFAULT '',
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER volunteers_touch BEFORE UPDATE ON volunteers FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();

CREATE TABLE partnerships (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  organization_id bigint NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  title text NOT NULL,
  kind text NOT NULL DEFAULT 'echange' CHECK (kind IN ('echange', 'soutien', 'coproduction', 'media', 'subvention')),
  contact_person_id bigint REFERENCES people(id) ON DELETE SET NULL,
  starts_on date,
  ends_on date,
  terms text NOT NULL DEFAULT '',
  value_eur numeric(10,2),
  status text NOT NULL DEFAULT 'discussion' CHECK (status IN ('discussion', 'signe', 'actif', 'termine')),
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER partnerships_touch BEFORE UPDATE ON partnerships FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();

-- Conducteur d'émission : la feuille de route minute par minute.
CREATE TABLE rundowns (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  show_id integer REFERENCES shows(id) ON DELETE SET NULL,
  episode_id bigint REFERENCES episodes(id) ON DELETE SET NULL,
  title text NOT NULL DEFAULT '',
  aired_on date,
  items jsonb NOT NULL DEFAULT '[]'::jsonb,
  status text NOT NULL DEFAULT 'brouillon' CHECK (status IN ('brouillon', 'pret', 'diffuse')),
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER rundowns_touch BEFORE UPDATE ON rundowns FOR EACH ROW EXECUTE FUNCTION regie_touch_updated_at();

-- Valorisation : sorties cochées et textes proposés pour un podcast, un événement, une émission.
CREATE TABLE promotions (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  entity_kind text NOT NULL,
  entity_id text NOT NULL,
  channel text NOT NULL CHECK (channel IN ('site', 'instagram', 'facebook', 'newsletter', 'antenne', 'partenaire')),
  text_proposal text NOT NULL DEFAULT '',
  done boolean NOT NULL DEFAULT false,
  done_at timestamptz,
  done_by uuid,
  UNIQUE (entity_kind, entity_id, channel)
);

CREATE TABLE listening_stats (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  at timestamptz NOT NULL DEFAULT now(),
  mount text NOT NULL,
  listeners integer NOT NULL DEFAULT 0,
  peak integer NOT NULL DEFAULT 0,
  title text NOT NULL DEFAULT ''
);
CREATE INDEX listening_stats_at_idx ON listening_stats (org_id, mount, at DESC);
