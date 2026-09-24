# Database contract

This project keeps complete provider, prediction, training, and job records in the `private` schema. Browser-facing reads use only the physical projection tables in `public` through the `public.live_matches` and `public.model_status` security-invoker views. The views never read `private` tables and expose no provider fixture IDs, private row IDs, raw provider payloads, model parameters, parameter hashes, request IDs, or sanitized internal errors.

## Data API exposure

Production must configure the Data API explicitly. New tables are not assumed to be exposed automatically.

- Expose only `public` and `private` when the server repository and service-only RPCs use PostgREST. Do not expose any additional application schema.
- Keep automatic exposure of new tables disabled. The local equivalent is `api.auto_expose_new_tables = false` in `supabase/config.toml`.
- `private` is present only for trusted server calls made as `service_role`. `PUBLIC`, `anon`, and `authenticated` have no schema usage, table access, or function execution there.
- Browser roles can select only the approved `public.live_match_projection` and `public.model_status_projection` rows, directly or through their security-invoker views. RLS permits public reads and there are no browser write policies.
- Never ship a secret or `service_role` key to a browser. A publishable client is constrained by the grants and RLS policies in the migration.

The production Dashboard setting is under **Project Settings > Data API > Exposed schemas**. Apply the migration and the Data API setting together; grants determine whether a role can reach an object, while RLS determines which rows that role can read.

## Exact grants

The migration applies these privileges:

- `service_role`: `USAGE` on `private`; `USAGE` on `private.model_state` and `private.run_state`; `SELECT`, `INSERT`, and `UPDATE` on every private table; `EXECUTE` on `private.claim_job(text,text,integer)`, `private.finish_job(uuid,uuid,text,jsonb,text)`, and `private.promote_model(uuid,uuid)`.
- `anon`, `authenticated`, and `service_role`: `USAGE` on `public`; `SELECT` on both projection tables and both security-invoker views.
- `service_role`: `INSERT` and `UPDATE` on both projection tables.
- `PUBLIC`, `anon`, and `authenticated`: no privileges on the `private` schema, its tables, sequences, enum types, or functions.
- `anon` and `authenticated`: no write privileges on either projection table; RLS supplies only a `SELECT USING (true)` policy.

No table uses an identity or serial sequence. UUID primary keys use `gen_random_uuid()`.

## Transactional RPCs

All RPCs are `SECURITY INVOKER`, set `search_path = ''`, explicitly require `current_user = 'service_role'`, and revoke execution from `PUBLIC`, `anon`, and `authenticated`.

- `claim_job` accepts leases from 30 through 900 seconds and atomically inserts a run or reacquires the same idempotency key only when its running lease has expired.
- `finish_job` requires both `run_id` and the current `request_id` fencing token. It accepts only terminal states and can finish only the matching running, unexpired lease; a worker holding the token from an expired lease cannot finish a reacquired row.
- `promote_model` locks the candidate, current model, and uniquely bound training run. The run must still be `running` and unfinished. Its train/validation arrays must contain at least 70/30 distinct, non-null, non-overlapping fixture IDs with confirmed outcomes, and every validation fixture must have provider-observation evidence later than every training outcome confirmation. Candidate counts and finite Brier/log-loss values must match the bound run, and both metrics must strictly improve. Promotion retires the prior model, activates the candidate, finalizes the run with `candidate promoted`, and refreshes the public model projection in the same transaction.

## Indexes and RLS

RLS is enabled on all seven private tables and both public projection tables. Access-path indexes cover fixture status/start, snapshot fixture/time, prediction fixture/time, training completion, and active job leases. Additional indexes cover every foreign key that is not already the leading column of a primary or unique index.

## Rollback rule

Schema migrations in this phase are additive. Do not roll back the database by deleting migration history or destructively dropping durable data. Model rollback is a separate, audited data-level transaction against immutable model versions; it must preserve the model and training audit trail and is not a schema rollback.
