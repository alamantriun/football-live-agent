alter table private.live_snapshots
  add constraint live_snapshots_id_fixture_id_unique
  unique (id, fixture_id);

alter table private.predictions
  add constraint predictions_snapshot_fixture_id_fkey
  foreign key (snapshot_id, fixture_id)
  references private.live_snapshots (id, fixture_id);
