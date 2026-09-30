-- This baseline exists only to produce clearly labelled live readings while
-- chronological evidence is collected. It intentionally has no validation
-- metrics and does not populate the public model-status projection.
insert into private.model_versions (
  version,
  state,
  parameters,
  parameter_hash,
  code_version,
  train_size,
  validation_size,
  activated_at
)
values (
  'baseline-live-v1',
  'active',
  '{
    "prior_home": 1.5,
    "prior_away": 1.2,
    "factores": {"local": 1.0, "visitante": 1.0},
    "evidence_status": "collecting_unvalidated"
  }'::jsonb,
  encode(digest('baseline-live-v1', 'sha256'), 'hex'),
  'bootstrap',
  0,
  0,
  now()
)
on conflict (version) do nothing;
