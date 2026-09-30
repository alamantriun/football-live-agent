create extension if not exists pg_cron;
create extension if not exists pg_net;

do $$
begin
  if not exists (select 1 from pg_extension where extname = 'pg_cron') then
    raise exception 'pg_cron must be enabled before scheduling collection jobs';
  end if;
  if not exists (select 1 from pg_extension where extname = 'pg_net') then
    raise exception 'pg_net must be enabled before scheduling collection jobs';
  end if;
  if not exists (
    select 1 from vault.secrets where name = 'football_api_base_url'
  ) or not exists (
    select 1 from vault.secrets where name = 'football_cron_secret'
  ) then
    raise exception 'Vault origin and cron secret must exist before scheduling jobs';
  end if;
end;
$$;

create or replace function private.dispatch_scheduled_job(
  p_path text,
  p_job_name text
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_origin text;
  v_secret text;
begin
  if (p_path, p_job_name) not in (
    ('/api/jobs/discover', 'discover'),
    ('/api/jobs/collect', 'collect'),
    ('/api/jobs/settle', 'settle'),
    ('/api/jobs/train', 'train')
  ) then
    raise exception 'unsupported scheduled job';
  end if;

  select decrypted_secret into strict v_origin
  from vault.decrypted_secrets
  where name = 'football_api_base_url';

  select decrypted_secret into strict v_secret
  from vault.decrypted_secrets
  where name = 'football_cron_secret';

  return net.http_post(
    url := rtrim(v_origin, '/') || p_path,
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'X-Cron-Secret', v_secret,
      'X-Idempotency-Key', p_job_name || ':' || to_char(
        date_trunc('minute', timezone('utc', now())),
        'YYYY-MM-DD"T"HH24:MI"Z"'
      )
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 25000
  );
end;
$$;

revoke all on function private.dispatch_scheduled_job(text, text)
  from public, anon, authenticated, service_role;

select cron.unschedule(jobid)
from cron.job
where jobname in (
  'football-live-discover',
  'football-live-collect',
  'football-live-settle',
  'football-live-train'
);

select cron.schedule(
  'football-live-discover',
  '*/2 * * * *',
  $$select private.dispatch_scheduled_job('/api/jobs/discover', 'discover');$$
);

select cron.schedule(
  'football-live-collect',
  '* * * * *',
  $$select private.dispatch_scheduled_job('/api/jobs/collect', 'collect');$$
);

select cron.schedule(
  'football-live-settle',
  '*/5 * * * *',
  $$select private.dispatch_scheduled_job('/api/jobs/settle', 'settle');$$
);

select cron.schedule(
  'football-live-train',
  '17 3 * * *',
  $$select private.dispatch_scheduled_job('/api/jobs/train', 'train');$$
);
