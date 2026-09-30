create function private.publish_live_match_prediction(p_prediction_id uuid)
returns void
language sql
security definer
set search_path = ''
as $$
  insert into public.live_match_projection (
    public_id,
    competition,
    home_name,
    away_name,
    home_logo_url,
    away_logo_url,
    scheduled_at,
    status,
    minute,
    score_home,
    score_away,
    data_status,
    provider_observed_at,
    collected_at,
    probabilities,
    explanation,
    model_version,
    prediction_created_at,
    updated_at
  )
  select
    f.public_id,
    f.competition,
    f.home_name,
    f.away_name,
    f.home_logo_url,
    f.away_logo_url,
    f.scheduled_at,
    f.status,
    s.minute,
    s.score_home,
    s.score_away,
    p.quality,
    s.provider_observed_at,
    s.collected_at,
    p.probabilities,
    p.explanation,
    m.version,
    p.created_at,
    now()
  from private.predictions p
  join private.fixtures f on f.id = p.fixture_id
  join private.live_snapshots s on s.id = p.snapshot_id
  join private.model_versions m on m.id = p.model_version_id
  where p.id = p_prediction_id
    and f.status = 'live'
  on conflict (public_id) do update
  set competition = excluded.competition,
      home_name = excluded.home_name,
      away_name = excluded.away_name,
      home_logo_url = excluded.home_logo_url,
      away_logo_url = excluded.away_logo_url,
      scheduled_at = excluded.scheduled_at,
      status = excluded.status,
      minute = excluded.minute,
      score_home = excluded.score_home,
      score_away = excluded.score_away,
      data_status = excluded.data_status,
      provider_observed_at = excluded.provider_observed_at,
      collected_at = excluded.collected_at,
      probabilities = excluded.probabilities,
      explanation = excluded.explanation,
      model_version = excluded.model_version,
      prediction_created_at = excluded.prediction_created_at,
      updated_at = excluded.updated_at
  where public.live_match_projection.prediction_created_at is null
     or excluded.prediction_created_at >= public.live_match_projection.prediction_created_at;
$$;

create function private.publish_live_match_prediction_trigger()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform private.publish_live_match_prediction(new.id);
  return new;
end;
$$;

create trigger publish_live_match_prediction_after_write
after insert or update on private.predictions
for each row execute function private.publish_live_match_prediction_trigger();

create function private.sync_live_match_fixture_trigger()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.status = 'live' then
    update public.live_match_projection
    set competition = new.competition,
        home_name = new.home_name,
        away_name = new.away_name,
        home_logo_url = new.home_logo_url,
        away_logo_url = new.away_logo_url,
        scheduled_at = new.scheduled_at,
        status = new.status,
        updated_at = now()
    where public_id = new.public_id;
  else
    delete from public.live_match_projection
    where public_id = new.public_id;
  end if;
  return new;
end;
$$;

create trigger sync_live_match_fixture_after_update
after update of status, competition, home_name, away_name,
  home_logo_url, away_logo_url, scheduled_at
on private.fixtures
for each row execute function private.sync_live_match_fixture_trigger();

select private.publish_live_match_prediction(latest.id)
from (
  select distinct on (fixture_id) id, fixture_id
  from private.predictions
  order by fixture_id, created_at desc
) as latest;

delete from public.live_match_projection projection
using private.fixtures fixture
where projection.public_id = fixture.public_id
  and fixture.status <> 'live';

revoke all on function private.publish_live_match_prediction(uuid)
  from public, anon, authenticated;
revoke all on function private.publish_live_match_prediction_trigger()
  from public, anon, authenticated;
revoke all on function private.sync_live_match_fixture_trigger()
  from public, anon, authenticated;

grant execute on function private.publish_live_match_prediction(uuid)
  to service_role;
