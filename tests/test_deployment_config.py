import json
from pathlib import Path


def test_security_headers_cover_all_routes():
    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    headers = {item["key"].lower(): item["value"] for item in config["headers"][0]["headers"]}

    assert "content-security-policy" in headers
    assert "unsafe-eval" not in headers["content-security-policy"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "https://cdn.freebiesupply.com" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_vercel_routes_keep_public_pages_and_api_in_the_same_origin():
    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    rewrites = {(rule["source"], rule["destination"]) for rule in config["rewrites"]}

    assert ("/app", "/app/index.html") in rewrites
    assert ("/modelo", "/modelo/index.html") in rewrites


def test_api_requests_keep_their_original_path_for_fastapi_routing():
    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))

    assert not any(rule["source"] == "/api/(.*)" for rule in config["rewrites"])


def test_initial_baseline_is_seeded_without_claiming_validated_metrics():
    migrations = list(Path("supabase/migrations").glob("*bootstrap_initial_baseline.sql"))

    assert len(migrations) == 1
    migration = migrations[0].read_text(encoding="utf-8")
    assert "insert into private.model_versions" in migration
    assert "baseline-live-v1" in migration
    assert "public.model_status_projection" not in migration


def test_scheduled_collection_reads_its_origin_and_secret_from_vault():
    migrations = list(Path("supabase/migrations").glob("*schedule_collection_jobs.sql"))

    assert len(migrations) == 1
    migration = migrations[0].read_text(encoding="utf-8")
    assert "create extension if not exists pg_cron" in migration
    assert "create extension if not exists pg_net" in migration
    assert "cron.schedule" in migration
    assert "vault.decrypted_secrets" in migration
    assert "football_api_base_url" in migration
    assert "football_cron_secret" in migration
    assert "X-Cron-Secret" in migration
    assert "football-live-agent.vercel.app" not in migration
