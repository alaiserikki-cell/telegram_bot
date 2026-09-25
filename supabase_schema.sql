-- Run once in Supabase: Dashboard -> SQL Editor -> New query -> paste -> Run.
-- The bot uses the service-role (secret) key server-side, which bypasses RLS.
-- RLS is enabled with no policies, so the public anon key can read nothing.

create table if not exists notes (
  note_id    text primary key,
  data       jsonb not null,
  updated_at timestamptz not null default now()
);

create table if not exists approved (
  name       text primary key,        -- YYYY-MM-DD_<slug>.md
  markdown   text not null,
  created_at timestamptz not null default now()
);

create table if not exists kv (
  key   text primary key,             -- e.g. 'redo': the draft Meera pressed Redo on
  value text not null
);

alter table notes    enable row level security;
alter table approved enable row level security;
alter table kv       enable row level security;

-- Handy view for looking at the log in the Supabase table editor.
create or replace view log as
select data->>'note_id'     as note_id,
       data->>'source'      as source,
       data->>'received_at' as received_at,
       data->>'verdict'     as verdict,
       (data->>'total_score')::int as total_score,
       data->>'drafted_at'  as drafted_at,
       data->>'decision'    as decision,
       data->>'decided_at'  as decided_at,
       (data->>'redo_count')::int as redo_count
from notes
order by data->>'received_at';
