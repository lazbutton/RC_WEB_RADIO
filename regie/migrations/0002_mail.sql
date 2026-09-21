-- Module Mails (Inbox Zero porté sur Postgres). Une ligne mail_items = un mail trié ; mail_index = toute la INBOX pour la recherche.

CREATE TABLE mail_items (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  imap_uid text NOT NULL,
  uidvalidity text NOT NULL,
  message_id text NOT NULL DEFAULT '',
  in_reply_to text NOT NULL DEFAULT '',
  sender text NOT NULL DEFAULT '',
  subject text NOT NULL DEFAULT '',
  excerpt text NOT NULL DEFAULT '',
  excerpt_clean text NOT NULL DEFAULT '',
  category text NOT NULL,
  reason text NOT NULL DEFAULT '',
  confidence real NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'moved', 'skipped', 'gone')),
  error text,
  draft text NOT NULL DEFAULT '',
  summary text NOT NULL DEFAULT '',
  summary_full text NOT NULL DEFAULT '',
  mailed_at timestamptz,
  notion_url text NOT NULL DEFAULT '',
  attachments jsonb NOT NULL DEFAULT '[]'::jsonb,
  seen boolean NOT NULL DEFAULT true,
  flagged boolean NOT NULL DEFAULT false,
  folder text NOT NULL DEFAULT 'INBOX',
  folder_uid text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  decided_at timestamptz,
  UNIQUE (org_id, uidvalidity, imap_uid)
);
CREATE INDEX mail_items_status_idx ON mail_items (org_id, status, mailed_at DESC);
CREATE INDEX mail_items_sender_idx ON mail_items (lower(sender));

CREATE TABLE mail_index (
  id bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  imap_uid text NOT NULL,
  uidvalidity text NOT NULL,
  folder text NOT NULL DEFAULT 'INBOX',
  message_id text NOT NULL DEFAULT '',
  sender text NOT NULL DEFAULT '',
  subject text NOT NULL DEFAULT '',
  body text NOT NULL DEFAULT '',
  summary text NOT NULL DEFAULT '',
  mailed_at timestamptz,
  item_id bigint REFERENCES mail_items(id) ON DELETE SET NULL,
  UNIQUE (org_id, uidvalidity, folder, imap_uid)
);
CREATE INDEX mail_index_item_idx ON mail_index (item_id);

CREATE TABLE mail_sender_memory (
  org_id uuid NOT NULL REFERENCES orgs(id),
  sender_email text NOT NULL,
  category text NOT NULL,
  hits integer NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, sender_email)
);
