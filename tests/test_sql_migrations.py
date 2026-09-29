import re
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = REPOSITORY_ROOT / "supabase" / "migrations"
TRAINING_HASH_FUNCTION = re.compile(
    r"create(?: or replace)? function private\.compute_training_hash\(.*?\n\$\$;",
    re.DOTALL,
)


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
