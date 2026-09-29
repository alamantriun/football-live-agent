# Task 6 report — controlled repository-backed model promotion

## Outcome

Implemented the repository-backed training path with a strict chronological split, exactly 30 later validation fixtures, consumed-holdout exclusion, finite Brier/log-loss scoring, strict comparison against both the active champion and a fixed no-adjustment baseline, canonical SHA-256 evidence hashes, immutable candidate evidence, atomic candidate/run/consumption recording, and transactional promotion through `private.promote_model`.

Rejected candidates and their terminal training runs are retained. Their validation fixture IDs are inserted into a normalized primary-key ledger in the same transaction, so a losing holdout cannot be reused and concurrent attempts cannot consume the same fixture twice.

`aprendizaje.py` remains the local SQLite/JSON compatibility entry point, but its split, fitting, scoring, and promotion gate now delegate to the production pure core in `football_live/training.py`.

Commit: `c9655b6 feat: add controlled model promotion`

## RED evidence

### Cloud learning core absent

Command:

```powershell
$env:PYTHONPATH=(Resolve-Path 'work/packages').Path
python -m pytest tests/test_training.py tests/test_repository.py -q
```

Observed:

```text
ERROR tests/test_training.py
ERROR tests/test_repository.py
ModuleNotFoundError: No module named 'football_live.training'
2 errors in 0.72s
```

### Legacy wrapper allowed an equal Brier score

Command:

```powershell
python -m pytest tests/test_training.py::test_local_compatibility_gate_uses_same_strict_both_metric_rule -q
```

Observed:

```text
FAILED test_local_compatibility_gate_uses_same_strict_both_metric_rule
AssertionError: assert not True
1 failed in 0.22s
```

### Consumed-ID read could truncate silently

Command:

```powershell
python -m pytest tests/test_repository.py::test_consumed_validation_ids_fail_closed_instead_of_truncating -q
```

Observed:

```text
FAILED test_consumed_validation_ids_fail_closed_instead_of_truncating
Failed: DID NOT RAISE RepositoryUnavailable
1 failed in 0.28s
```

### SQL harness unavailable locally

Command:

```powershell
supabase test db
```

Observed:

```text
supabase: The term 'supabase' is not recognized
```

`npx.cmd --no-install supabase --version` also confirmed that no cached CLI package exists. No package was installed and no remote service was accessed, per task constraints.

## GREEN evidence

### New training and repository contracts

```text
python -m pytest tests/test_training.py tests/test_repository.py -q
57 passed in 0.25s
```

### Directed learning/model regressions

Command:

```powershell
python -m pytest tests/test_training.py tests/test_repository.py test_modelo_probabilistico.py test_vivo_confiable.py -q
```

Observed:

```text
84 passed in 1.21s
```

### Filtered full suite

Command:

```powershell
python -m pytest -q -k "not test_registro_no_cuenta_repeticiones_ni_predicciones_finales"
```

Observed:

```text
142 passed, 1 deselected in 6.68s
```

The unchanged legacy `tmp_path` test was the only deselection, as permitted by the task.

### Final unfiltered suite

```text
python -m pytest -q
143 passed in 7.21s
```

### Static checks

- `python -m compileall -q football_live aprendizaje.py` completed successfully.
- `git diff --check` found no whitespace errors (only existing Windows LF-to-CRLF notices).
- The new SQL file declares `plan(26)` and contains 26 pgTAP assertions.

## Self-review

- Confirmed train/validation ordering is deterministic by first observation and fixture UUID; all training outcomes must be confirmed strictly before the validation cutoff.
- Confirmed consumed IDs are excluded from future validation selection. A consumed fixture may become later training evidence only after its outcome precedes the new cutoff; it is never reused as holdout evidence.
- Confirmed candidate, champion, and baseline are scored on the same validation fixtures and every metric must be finite. Equality or improvement in only one metric rejects the candidate.
- Confirmed the canonical SHA-256 payload uses recursively sorted parameter keys while preserving chronological train-ID and validation-ID order and includes the code version.
- Confirmed `record_training_evaluation` inserts the model version, run, and 30 consumption rows transactionally before promotion. Rejection is terminal; acceptance remains `candidate/running` until `promote_model` succeeds.
- Added a database trigger that makes canonical Task 6 model evidence immutable while retaining compatibility for legacy noncanonical Task 2 fixtures.
- Added `predictions.created_at < outcomes.confirmed_at` to prevent a prediction generated after the result from entering training evidence.
- Changed consumed-ID reads to request one sentinel row beyond the configured bound and fail closed instead of silently forgetting holdouts.
- Updated Task 2 pgTAP seed metrics to include candidate/champion/baseline evidence required by the strengthened promotion RPC.
- No remote service, Supabase project, Docker daemon, or network package installation was used.

## Changed files

- `.superpowers/sdd/2026-09-24-football-live-product-implementation/task-6-report.md`
- `aprendizaje.py`
- `football_live/repository.py`
- `football_live/supabase_gateway.py`
- `football_live/training.py`
- `supabase/migrations/20260928153000_add_controlled_training.sql`
- `supabase/tests/001_live_product_schema.sql`
- `supabase/tests/002_controlled_training.sql`
- `tests/test_repository.py`
- `tests/test_training.py`

## Remaining concern

The Python behavior is locally verified. The migration and 26 pgTAP assertions are authored and statically reviewed, but could not be executed because this checkout has neither a Supabase CLI nor a local PostgreSQL/Docker harness, and remote access was explicitly prohibited. They must be run in the approved local/dedicated Supabase test environment before applying the migration to production.

---

## Fix round 1/5 — durable training evidence

### Outcome

Addressed every round-one review finding before remote migration:

- consumed validation IDs now use deterministic `fixture_id` range pagination in pages of at most 1,000 rows, continue through exact server-cap multiples, and query a one-row sentinel at the hard 50,000-row boundary to fail closed;
- the service reconciles every promotion exception through a service-only terminal RPC, recognizing an already committed promotion and otherwise rejecting the still-pending candidate while marking its run failed;
- each training run starts by recovering candidate-bound `running` evaluations older than 900 seconds, in batches capped at 100;
- model and run evidence are immutable, terminal runs cannot be reopened, and only valid one-way lifecycle transitions remain writable;
- PostgreSQL now computes and stores the authoritative SHA-256 from canonical parameters, lexically sorted train UUIDs, lexically sorted validation UUIDs, and code version; callers no longer submit a version or hash;
- the original `private.promote_model(uuid, uuid)` signature remains unchanged, including legacy Task 2 success behavior;
- pgTAP coverage grew from 26 to 53 assertions for invoker security, empty search paths, explicit `current_user`, grants, immutable evidence, SQL hash binding, rollback behavior, reconciliation, stale cleanup, and consumed-holdout retention.

### RED evidence

Pagination and canonical-order tests initially failed against the single capped query and order-sensitive hash:

```text
python -m pytest tests/test_repository.py::test_consumed_validation_ids_use_service_only_bounded_view tests/test_repository.py::test_consumed_validation_ids_pages_past_exact_server_cap_multiple tests/test_repository.py::test_consumed_validation_ids_fail_closed_past_total_bound tests/test_training.py::test_canonical_hash_sorts_parameters_and_both_evidence_id_sets -q
4 failed
```

Recovery tests then failed because the service did not run stale cleanup or reconcile uncertain promotion responses:

```text
python -m pytest tests/test_training.py::test_service_persists_immutable_candidate_before_promotion tests/test_training.py::test_service_recognizes_promotion_committed_before_uncertain_response tests/test_training.py::test_service_finalizes_uncommitted_candidate_after_promotion_exception tests/test_repository.py::test_training_recovery_rpcs_map_terminal_state_and_use_safe_bounds -q
4 failed
```

The terminal gateway test also proved recovered evidence was initially discarded:

```text
python -m pytest tests/test_repository.py::test_training_recovery_rpcs_map_terminal_state_and_use_safe_bounds -q
FAILED ... assert () == (UUID('00000000-0000-0000-0000-000000000015'),)
1 failed in 0.30s
```

The SQL contract was RED by inspection before implementation: the migration still contained `p_parameter_hash`, accepted caller-owned `p_version`, and had no `finalize_training_evaluation`, `recover_abandoned_training_evaluations`, or `training_runs_immutable_evidence` definitions.

### GREEN evidence

Directed learning, repository, probabilistic-model, and live-quality regressions:

```text
$env:PYTHONPATH=(Resolve-Path 'work/packages').Path
python -m pytest tests/test_training.py tests/test_repository.py test_modelo_probabilistico.py test_vivo_confiable.py -q
89 passed in 1.54s
```

Filtered full suite, excluding only the permitted unchanged legacy `tmp_path` test:

```text
$env:PYTHONPATH=(Resolve-Path 'work/packages').Path
python -m pytest -q -k "not test_registro_no_cuenta_repeticiones_ni_predicciones_finales"
147 passed, 1 deselected in 7.21s
```

Unfiltered full suite:

```text
$env:PYTHONPATH=(Resolve-Path 'work/packages').Path
python -m pytest -q
148 passed in 7.02s
```

Static verification:

```text
python -m compileall -q football_live aprendizaje.py
completed successfully

SQL contract checks
assertions 53
balanced_dollar_quotes=True
pgtap_plan_matches=True
no_auth_role=True
preserved_promote_signature=True
caller_hash_removed=True
recovery_and_finalize=True
bounded_recovery=True

git diff --check
no whitespace errors; only Windows LF-to-CRLF notices
```

The local SQL harness remains unavailable:

```text
supabase test db
supabase: The term 'supabase' is not recognized as a name of a cmdlet, function, script file, or executable program.
```

No CLI package, database, remote service, or subagent was used.

### Self-review

- Pagination uses inclusive ranges `(0,999)`, `(1000,1999)`, and so on. An exact multiple triggers the next sentinel read, and 50,001 rows fail rather than truncate. Fake-client tests enforce both the 1,000-row server cap and hard upper bound.
- Recovery runs before reading the champion or selecting evidence. Reconciliation locks the candidate before the run, matching promotion lock order and avoiding a model/run lock inversion.
- `record_training_evaluation` remains one transaction for candidate, run, and all 30 consumption rows. A duplicate ledger insert rolls back both candidate and run; pgTAP checks both absences.
- The run trigger freezes IDs, timestamps, candidate binding, metrics, and hash, permits only `running` to terminal transitions with `finished_at`, and prevents reopening terminal evidence. Existing table privileges still exclude delete; the invoker RPC has only the service role grant.
- Canonical SQL hashing recursively orders object keys and sorts both UUID sets. The fixed pgTAP digest `9a8c2e8920d02b731f9ff5c77fbe4040fb3d59b05a239e164c493c38e519fa81` independently matches the Python implementation for reversed input IDs.
- New lifecycle functions are `SECURITY INVOKER`, use an empty `search_path`, explicitly check `current_user = 'service_role'`, avoid `auth.role()`, and deny `PUBLIC`, `anon`, and `authenticated` execution. This follows the Supabase security guidance while honoring the no-remote constraint.
- Recovery ignores legacy running rows without a candidate binding, enforces a minimum age of 900 seconds and maximum batch of 100, and keeps every consumed validation ID after failure cleanup.
- Task 2's rollback-only pgTAP fixture temporarily disables the new immutability triggers solely to construct malformed legacy states; production triggers remain enabled and Task 6 exercises promotion with them active.

### Changed files in fix round 1

- `.superpowers/sdd/2026-09-24-football-live-product-implementation/task-6-report.md`
- `football_live/repository.py`
- `football_live/supabase_gateway.py`
- `football_live/training.py`
- `supabase/migrations/20260928153000_add_controlled_training.sql`
- `supabase/tests/001_live_product_schema.sql`
- `supabase/tests/002_controlled_training.sql`
- `tests/test_repository.py`
- `tests/test_training.py`

### Remaining concern

The Python and static SQL contracts are verified locally, but the 53 pgTAP assertions could not be executed without a local Supabase/PostgreSQL harness. They must pass in the approved isolated database environment before the still-unapplied migration is sent remotely.

## Fix round 2/5 - close training concurrency gaps

### Outcome

Addressed every round-two review finding without applying the migration remotely:

- every evaluation now receives a caller-generated durable run UUID before the record RPC, so an uncertain record response can be reconciled by identity;
- record-time uniqueness conflicts are contained in a PostgreSQL subtransaction: candidate, run, and partial holdout claims roll back together, then a terminal `failed` audit run is inserted and returned;
- canonical-hash advisory locking serializes identical evaluations, so two identical accepted attempts produce one candidate-owning winner and one terminal loser, with exactly one set of 30 consumed validation fixtures;
- a stale-champion attempt after a competing promotion is recorded as a terminal failed run without claiming holdout evidence;
- `TrainingService` now reconciles record exceptions as well as promotion exceptions and returns terminal collision outcomes without attempting promotion;
- new controlled candidates must use PostgreSQL-computed canonical 64-hex hashes; the only compatibility path is explicit `legacy_task2` provenance assigned to pre-existing/rollback-only Task 2 fixtures, not a hash-shape heuristic;
- direct table mutation is narrowed to the exact columns required by the `SECURITY INVOKER` RPCs, while triggers keep evidence, model IDs, and provenance immutable;
- pgTAP now denies `PUBLIC`, `anon`, and `authenticated` for record, promote, finalize, and recover, and expands concurrency/collision/provenance coverage to 67 assertions.

### RED evidence

The first focused test run proved that record attempts lacked a durable caller-owned identity, record exceptions were not reconciled, collision rows could not omit a candidate, and failed reconciliation returned a fake `None` active model:

```text
python -m pytest tests/test_training.py::test_service_reconciles_uncertain_record_response_by_preassigned_run_id tests/test_training.py::test_service_returns_terminal_record_collision_without_promotion tests/test_repository.py::test_record_training_evaluation_sends_preassigned_run_id tests/test_repository.py::test_record_collision_can_return_a_terminal_run_without_a_candidate tests/test_repository.py::test_finalize_failed_evaluation_reports_actual_active_model -q
5 failed
```

After those fixes, one additional focused test exposed that terminal record-collision responses still reported the prior model rather than the database's current active model:

```text
python -m pytest tests/test_repository.py::test_record_collision_can_return_a_terminal_run_without_a_candidate -q
FAILED ... expected MODEL_ID, got CURRENT_MODEL_ID
1 failed
```

Both focused RED sets were made green before broader regression testing:

```text
5 passed in 0.20s
1 passed in 0.15s
```

### GREEN evidence

Directed learning, repository, probabilistic-model, and live-quality regressions after implementation:

```text
$env:PYTHONPATH=(Resolve-Path 'work/packages').Path
python -m pytest tests/test_training.py tests/test_repository.py test_modelo_probabilistico.py test_vivo_confiable.py -q
93 passed in 1.40s
```

Filtered full suite, excluding only the permitted unchanged legacy `tmp_path` test:

```text
$env:PYTHONPATH=(Resolve-Path 'work/packages').Path
python -m pytest -q -k "not test_registro_no_cuenta_repeticiones_ni_predicciones_finales"
151 passed, 1 deselected in 7.56s
```

Unfiltered full suite:

```text
$env:PYTHONPATH=(Resolve-Path 'work/packages').Path
python -m pytest -q
152 passed in 7.56s
```

Static SQL contract checks:

```text
assertions 67
balanced_dollar_quotes=True
pgtap_plan_matches=True
run_id_signature=True
collision_subtransaction=True
concurrency_lock=True
explicit_provenance=True
model_id_immutable=True
least_privilege=True
no_auth_role=True
```

The local SQL harness is still unavailable and no remote substitute was used:

```text
supabase test db
supabase: The term 'supabase' is not recognized as a name of a cmdlet, function, script file, or executable program.
```

### Self-review

- Lock order is deterministic: the record RPC takes transaction-scoped advisory locks for the run UUID and then canonical hash before reading/locking the active champion. Identical evidence serializes on the hash, while same-run retries serialize on the run UUID and return idempotently only when immutable evidence matches.
- Candidate creation, running-run creation, and all validation claims remain atomic inside one nested block. Known uniqueness conflicts roll back that block only; the outer transaction records a terminal failed audit run. Unexpected uniqueness errors are re-raised rather than misclassified.
- The terminal loser has no candidate binding and consumes no validation rows. The winning run alone owns the candidate and the 30-row holdout ledger, preventing duplicate evaluation evidence without weakening the winner transaction.
- The finalization RPC recognizes candidate-less terminal failures before requiring a candidate, allowing record-response uncertainty to resolve durably. Gateway reconciliation obtains the real active model for failed outcomes, matching production and fake-service behavior.
- Promotion no longer treats malformed hashes as legacy. Controlled rows must match the exact PostgreSQL recomputation over persisted parameters, sorted train IDs, sorted validation IDs, and code version. Legacy behavior is fenced by explicit provenance populated for pre-migration rows and unavailable to service-role inserts.
- Table-wide service-role `INSERT`/`UPDATE` privileges are revoked. Only RPC-required lifecycle/evidence columns remain writable, and immutable triggers reject changes to model ID, provenance, candidate binding, evidence IDs, metrics, hashes, and timestamps.
- All four training RPCs remain `SECURITY INVOKER`, empty-search-path, explicit-`current_user` functions with execute denied to `PUBLIC`, `anon`, and `authenticated` and granted only to `service_role`.
- The migration remains unapplied remotely. Task 2's original `private.promote_model(uuid, uuid)` signature and successful behavior are retained through explicit rollback-only legacy fixtures.

### Changed files in fix round 2

- `.superpowers/sdd/2026-09-24-football-live-product-implementation/task-6-report.md`
- `football_live/repository.py`
- `football_live/supabase_gateway.py`
- `football_live/training.py`
- `supabase/migrations/20260928153000_add_controlled_training.sql`
- `supabase/tests/001_live_product_schema.sql`
- `supabase/tests/002_controlled_training.sql`
- `tests/test_repository.py`
- `tests/test_training.py`

### Remaining concern

Python behavior and static SQL contracts are verified locally. The 67 pgTAP assertions still require execution in an approved isolated Supabase/PostgreSQL test environment before the unapplied migration is promoted.
