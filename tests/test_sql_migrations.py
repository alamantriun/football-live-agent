import re
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = REPOSITORY_ROOT / "supabase" / "migrations"
TRAINING_HASH_FUNCTION = re.compile(
    r"create(?: or replace)? function private\.compute_training_hash\(.*?\n\$\$;",
    re.DOTALL,
)
MATCH_HISTORY_MIGRATION = "*_add_match_history_predictions.sql"


@pytest.mark.parametrize(
    "migration_name",
    (
        "20260928153000_add_controlled_training.sql",
        "20260929155047_fix_training_hash_schema.sql",
    ),
)
def test_training_hash_binds_pgcrypto_digest_to_extensions_schema(
    migration_name: str,
):
    migration_path = MIGRATIONS / migration_name
    assert migration_path.is_file(), f"missing migration: {migration_name}"

    sql = migration_path.read_text(encoding="utf-8")
    definition = TRAINING_HASH_FUNCTION.search(sql)
    assert definition is not None, "compute_training_hash definition is missing"
    assert "extensions.digest(" in definition.group()
    assert "public.digest(" not in definition.group()


def _match_history_sql() -> str:
    matches = list(MIGRATIONS.glob(MATCH_HISTORY_MIGRATION))
    assert len(matches) == 1, "match-history RPC migration is missing or ambiguous"
    return matches[0].read_text(encoding="utf-8").lower()


def test_match_history_rpc_has_private_explicit_service_only_contract():
    sql = _match_history_sql()

    assert "function private.match_history_predictions(" in sql
    assert "p_fixture_id uuid" in sql
    assert "p_limit integer default 90" in sql
    assert "returns table (" in sql
    for column in (
        "snapshot_id uuid",
        "probabilities jsonb",
        "lambda_adjusted jsonb",
        "created_at timestamptz",
        "model_version text",
    ):
        assert column in sql
    assert "security invoker" in sql
    assert "set search_path = ''" in sql
    assert (
        "revoke all on function private.match_history_predictions(uuid, integer) "
        "from public, anon, authenticated, service_role;"
    ) in sql
    assert (
        "grant execute on function private.match_history_predictions(uuid, integer) "
        "to service_role;"
    ) in sql
    assert "create table" not in sql
    assert "create view" not in sql
    assert "create policy" not in sql


def test_match_history_rpc_bounds_snapshots_then_ranks_one_prediction_each():
    sql = _match_history_sql()

    assert "least(greatest(coalesce(p_limit, 90), 1), 90)" in sql
    assert "row_number() over" in sql
    assert "partition by predictions.snapshot_id" in sql
    assert "(models.state = 'active') desc" in sql
    assert "predictions.created_at desc" in sql
    assert "predictions.id desc" in sql
    assert "where ranked.preference = 1" in sql
