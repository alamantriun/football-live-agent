create table private.match_events (
  id uuid primary key default gen_random_uuid(),
  fixture_id uuid not null references private.fixtures(id) on delete cascade,
  provider_event_order integer not null check (provider_event_order between 1 and 10000),
  minute smallint not null check (minute between 0 and 150),
  added_time smallint check (added_time between 0 and 30),
  side text not null check (side in ('home', 'away')),
  kind text not null check (kind in ('goal', 'yellow_card', 'red_card')),
  first_observed_at timestamptz not null default now(),
  last_observed_at timestamptz not null default now(),
  unique (fixture_id, provider_event_order)
);

create index match_events_fixture_timeline_idx
  on private.match_events (
    fixture_id,
    minute,
    added_time nulls last,
    provider_event_order
  );

create function private.refresh_match_event_last_observed_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.last_observed_at := clock_timestamp();
  return new;
end;
$$;

create trigger match_events_refresh_last_observed_at
before update on private.match_events
for each row
execute function private.refresh_match_event_last_observed_at();

alter table private.match_events enable row level security;

revoke all on private.match_events from public, anon, authenticated, service_role;
grant select, insert, update on private.match_events to service_role;

revoke all on function private.refresh_match_event_last_observed_at()
  from public, anon, authenticated, service_role;
