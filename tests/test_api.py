from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

import pytest
from starlette.testclient import TestClient

from football_live.api import create_app
from football_live.logging import RedactingFormatter
from football_live.repository import RepositoryUnavailable
from football_live.settings import Settings


NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
PUBLIC_ID = UUID("10000000-0000-0000-0000-000000000001")
REQUEST_ID = UUID("20000000-0000-0000-0000-000000000001")


def live_row(**changes):
    row = {
        "public_id": str(PUBLIC_ID),
        "competition": "Premier League",
        "home_name": "Arsenal",
        "away_name": "Chelsea",
        "home_logo_url": "https://cdn.example.test/arsenal.png",
        "away_logo_url": "https://cdn.example.test/chelsea.png",
        "scheduled_at": NOW.isoformat(),
        "status": "live",
        "minute": 61,
        "score_home": 1,
        "score_away": 0,
        "data_status": "degraded",
        "provider_observed_at": NOW.isoformat(),
        "collected_at": NOW.isoformat(),
        "probabilities": {"home": 52.0, "draw": 28.0, "away": 20.0},
        "explanation": {"warnings": ["Some source signals are unavailable."]},
        "model_version": "v2026.09.30",
        "prediction_created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    row.update(changes)
    return row


class FakeRepository:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.live_calls: list[tuple[int, str | None]] = []
        self.match_calls: list[UUID] = []
        self.model_calls = 0

    def public_live(self, limit, cursor):
        if self.unavailable:
            raise RepositoryUnavailable("supabase secret response and traceback")
        self.live_calls.append((limit, cursor))
        return [live_row()]

    def public_match(self, public_id):
        self.match_calls.append(public_id)
        return live_row() if public_id == PUBLIC_ID else None

    def public_model_status(self):
        self.model_calls += 1
        return {
            "version": "v2026.09.30",
            "train_size": 70,
            "validation_size": 30,
            "brier": 0.19,
            "log_loss": 0.56,
            "activated_at": NOW.isoformat(),
            "last_training_finished_at": NOW.isoformat(),
            "last_training_decision": "promoted",
            "updated_at": NOW.isoformat(),
        }

    def claim_job(self, *_args):
        return None


class FakeProvider:
    async def list_live(self):
        raise AssertionError("duplicate job must not call provider")

    async def get_snapshot(self, _fixture_id):
        raise AssertionError("duplicate job must not call provider")


@pytest.fixture
def settings():
    return Settings(
        environment="test",
        cron_secret="test-cron-secret",
        allowed_hosts="testserver,localhost",
    )


@pytest.fixture
def repository():
    return FakeRepository()


@pytest.fixture
def client(settings, repository):
    app = create_app(settings, repository, FakeProvider())
    with TestClient(app) as test_client:
        yield test_client


def test_live_returns_only_public_projection_in_a_request_envelope(client, repository):
    response = client.get(
        "/api/live?limit=12&cursor=2026-09-30T11:59:00Z",
        headers={"X-Request-ID": str(REQUEST_ID)},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == str(REQUEST_ID)
    body = response.json()
    assert body["request_id"] == str(REQUEST_ID)
    assert body["data_status"] == "degraded"
    assert body["model_version"] == "v2026.09.30"
    assert body["items"][0]["public_id"] == str(PUBLIC_ID)
    assert "provider_fixture_id" not in body["items"][0]
    assert repository.live_calls == [(12, "2026-09-30T11:59:00Z")]


def test_live_rejects_oversized_limit_before_querying_repository(client, repository):
    response = client.get("/api/live?limit=51")

    assert response.status_code == 422
    assert repository.live_calls == []


def test_match_validates_public_uuid_and_returns_not_found_for_absent_match(client):
    assert client.get("/api/matches/not-a-uuid").status_code == 422

    response = client.get(f"/api/matches/{UUID(int=999)}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_model_status_and_health_are_public_and_do_not_expose_docs(client):
    model = client.get("/api/model/status")
    health = client.get("/api/health")

    assert model.status_code == 200
    assert model.json()["model_version"] == "v2026.09.30"
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_internal_job_rejects_missing_secret(client):
    response = client.post(
        "/api/jobs/discover", headers={"X-Idempotency-Key": "discover:1"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert "secret" not in response.text.lower()


def test_internal_job_rejects_invalid_idempotency_key_and_body(client):
    unauthorized = client.post(
        "/api/jobs/discover",
        headers={"X-Cron-Secret": "test-cron-secret"},
    )
    malformed = client.post(
        "/api/jobs/discover",
        headers={
            "X-Cron-Secret": "test-cron-secret",
            "X-Idempotency-Key": "discover: contains whitespace",
        },
        json={"unexpected": True},
    )

    assert unauthorized.status_code == 422
    assert malformed.status_code == 422


def test_internal_duplicate_job_returns_result_without_provider_work(client):
    response = client.post(
        "/api/jobs/discover",
        headers={
            "X-Cron-Secret": "test-cron-secret",
            "X-Idempotency-Key": "discover:2026-09-30T12:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["status"] == "duplicate"
    assert response.headers["Cache-Control"] == "no-store"


def test_public_error_is_sanitized(settings):
    app = create_app(settings, FakeRepository(unavailable=True), FakeProvider())
    with TestClient(app) as client:
        response = client.get("/api/live")

    assert response.status_code == 503
    body = response.json()
    assert "traceback" not in response.text.lower()
    assert "supabase" not in body["error"]["message"].lower()
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]


def test_untrusted_host_is_rejected(settings):
    app = create_app(settings, FakeRepository(), FakeProvider())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/health", headers={"Host": "attacker.test"})

    assert response.status_code == 400


def test_log_formatter_redacts_sensitive_keys_and_truncates_external_values():
    formatter = RedactingFormatter("%(message)s")
    record = logging.LogRecord(
        "football-live",
        logging.INFO,
        __file__,
        1,
        "request headers=%s",
        ({"Authorization": "token", "payload": "x" * 400},),
        None,
    )

    rendered = formatter.format(record)

    assert "token" not in rendered
    assert "[REDACTED]" in rendered
    assert len(rendered) < 380
