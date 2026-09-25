from datetime import datetime, timezone
import inspect
from uuid import UUID

import pytest
from pydantic import ValidationError

from football_live import domain
from football_live.domain import Fixture, LiveSnapshot, ModelVersion, PredictionRecord
from football_live.repository import Repository, RepositoryUnavailable
from football_live.settings import Settings
from football_live.supabase_gateway import SupabaseGateway


FIXTURE_ID = UUID("4b1c7cb7-7ce5-4fc4-bf89-4744c80a31b1")
SNAPSHOT_ID = UUID("22b244f7-771a-4ea3-953d-bfd828743a45")
MODEL_ID = UUID("bb9ad924-88d5-4b87-9af4-143c3bda250e")
RUN_ID = UUID("3746b572-e1fe-4131-a25b-7e33c92d8efe")
FENCING_REQUEST_ID = UUID("8d69f642-7623-4779-bebd-85d6fc167553")
HTTP_REQUEST_ID = UUID("cec47ae5-66e1-445d-9073-78560b471e42")
OBSERVED_AT = datetime(2026, 9, 24, 20, 3, 44, tzinfo=timezone.utc)
MODEL_CREATED_AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, data=None):
        self.data = [] if data is None else data


class FakeQuery:
    def __init__(self, client, schema, kind, name, params=None):
        self.client = client
        self.operation = {
            "schema": schema,
            "kind": kind,
            "name": name,
            "params": params,
        }

    def select(self, columns):
        self.operation["select"] = columns
        return self

    def upsert(self, payload, *, on_conflict):
        self.operation["payload"] = payload
        self.operation["on_conflict"] = on_conflict
        return self

    def insert(self, payload):
        self.operation["payload"] = payload
        return self

    def eq(self, column, value):
        self.operation.setdefault("filters", []).append(("eq", column, value))
        return self

    def lt(self, column, value):
        self.operation.setdefault("filters", []).append(("lt", column, value))
        return self

    def order(self, column, *, desc=False):
        self.operation["order"] = (column, desc)
        return self

    def limit(self, value):
        self.operation["limit"] = value
        return self

    def execute(self):
        self.client.operations.append(self.operation)
        if self.client.failure is not None:
            raise self.client.failure
        key = (
            self.operation["schema"],
            self.operation["kind"],
            self.operation["name"],
        )
        return FakeResponse(self.client.responses.get(key, []))


class FakeSchema:
    def __init__(self, client, name):
        self.client = client
        self.name = name

    def table(self, name):
        return FakeQuery(self.client, self.name, "table", name)

    def rpc(self, name, params):
        return FakeQuery(self.client, self.name, "rpc", name, params)


class FakeClient:
    def __init__(self):
        self.operations = []
        self.responses = {}
        self.failure = None

    def schema(self, name):
        return FakeSchema(self, name)


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def gateway(fake_client):
    settings = Settings(
        environment="test",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="server-secret",
        cron_secret="cron-secret",
    )
    return SupabaseGateway(settings, client_factory=lambda url, key: fake_client)


@pytest.fixture
def fixture():
    return Fixture(
        id=FIXTURE_ID,
        public_id="337a09f3-a806-4e56-a068-d758f74a78cb",
        provider="365scores",
        provider_fixture_id="provider-42",
        competition="Premier League",
        home_name="Arsenal",
        away_name="Chelsea",
        home_logo_url="https://cdn.example/arsenal.png",
        away_logo_url=None,
        scheduled_at=datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc),
        status="live",
        discovered_at=datetime(2026, 9, 24, 19, 55, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 24, 20, 3, tzinfo=timezone.utc),
    )


@pytest.fixture
def snapshot():
    return LiveSnapshot(
        id=SNAPSHOT_ID,
        fixture_id=FIXTURE_ID,
        minute=63,
        score_home=1,
        score_away=1,
        stats={"shots_on_target": {"home": 4, "away": None}},
        sanitized_provider_data={"clock": "63:44"},
        provider_observed_at=OBSERVED_AT,
        quality="degraded",
    )


@pytest.fixture
def prediction():
    return PredictionRecord(
        fixture_id=FIXTURE_ID,
        snapshot_id=SNAPSHOT_ID,
        model_version_id=MODEL_ID,
        lambda_base={"home": 1.2, "away": 0.9},
        lambda_adjusted={"home": 1.3, "away": 0.8},
        probabilities={"home": 45.0, "draw": 30.0, "away": 25.0},
        explanation={"signals": ["shots_on_target"]},
        quality="fresh",
    )


@pytest.fixture
def model_row():
    return {
        "id": str(MODEL_ID),
        "version": "2026.09.24.1",
        "state": "active",
        "parameters": {"home_advantage": 1.08},
        "parameter_hash": "sha256:abc123",
        "code_version": "2ebd55e",
        "previous_model_id": None,
        "train_size": 70,
        "validation_size": 30,
        "brier": 0.19,
        "log_loss": 0.61,
        "created_at": MODEL_CREATED_AT.isoformat(),
        "activated_at": OBSERVED_AT.isoformat(),
    }


def test_snapshot_bucket_is_stable_per_fixture_and_minute():
    observed_at = OBSERVED_AT
    snapshot = LiveSnapshot(
        fixture_id=FIXTURE_ID,
        minute=63,
        score_home=1,
        score_away=1,
        stats={"shots_on_target": {"home": 4, "away": None}},
        provider_observed_at=observed_at,
        quality="degraded",
    )
    assert snapshot.observation_bucket.isoformat() == "2026-09-24T20:03:00+00:00"
    assert snapshot.provider_observed_at is observed_at
    assert snapshot.provider_observed_at.second == 44
    assert snapshot.stats["shots_on_target"]["away"] is None


def test_domain_contracts_forbid_extra_fields_and_naive_datetimes(fixture):
    with pytest.raises(ValidationError):
        Fixture(**fixture.model_dump(), equipo_local="Arsenal")

    with pytest.raises(ValidationError):
        LiveSnapshot(
            fixture_id=FIXTURE_ID,
            minute=1,
            score_home=0,
            score_away=0,
            stats={},
            provider_observed_at=datetime(2026, 9, 24, 20, 0),
            quality="fresh",
        )


@pytest.mark.parametrize("invalid_probability", [-0.01, 100.01])
def test_prediction_probabilities_are_bounded(invalid_probability):
    with pytest.raises(ValidationError):
        PredictionRecord(
            fixture_id=FIXTURE_ID,
            snapshot_id=SNAPSHOT_ID,
            model_version_id=MODEL_ID,
            lambda_base={"home": 1.2, "away": 0.9},
            lambda_adjusted={"home": 1.3, "away": 0.8},
            probabilities={
                "home": invalid_probability,
                "draw": 25.0,
                "away": 25.0,
            },
            explanation={},
            quality="fresh",
        )


@pytest.mark.parametrize("field", ["lambda_base", "lambda_adjusted"])
@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_prediction_lambdas_reject_non_finite_values(field, non_finite, prediction):
    payload = prediction.model_dump()
    payload[field]["home"] = non_finite

    with pytest.raises(ValidationError):
        PredictionRecord.model_validate(payload)


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_prediction_probabilities_reject_non_finite_values(
    non_finite, prediction
):
    payload = prediction.model_dump()
    payload["probabilities"]["home"] = non_finite

    with pytest.raises(ValidationError):
        PredictionRecord.model_validate(payload)


def test_home_away_values_reject_provider_or_spanish_aliases():
    with pytest.raises(ValidationError):
        PredictionRecord(
            fixture_id=FIXTURE_ID,
            snapshot_id=SNAPSHOT_ID,
            model_version_id=MODEL_ID,
            lambda_base={"local": 1.2, "visitante": 0.9},
            lambda_adjusted={"home": 1.3, "away": 0.8},
            probabilities={"home": 40, "draw": 30, "away": 30},
            explanation={},
            quality="fresh",
        )

    with pytest.raises(ValidationError):
        LiveSnapshot(
            fixture_id=FIXTURE_ID,
            minute=1,
            score_home=0,
            score_away=0,
            stats={"shots": {"team1": 3, "team2": 1}},
            provider_observed_at=OBSERVED_AT,
            quality="fresh",
        )


def test_nonempty_probabilities_require_canonical_one_x_two_keys():
    with pytest.raises(ValidationError):
        PredictionRecord(
            fixture_id=FIXTURE_ID,
            snapshot_id=SNAPSHOT_ID,
            model_version_id=MODEL_ID,
            lambda_base={"home": 1.2, "away": 0.9},
            lambda_adjusted={"home": 1.3, "away": 0.8},
            probabilities={"team1": 45.0, "draw": 30.0, "team2": 25.0},
            explanation={},
            quality="fresh",
        )


def test_probability_total_allows_rounding_and_derived_markets():
    prediction = PredictionRecord(
        fixture_id=FIXTURE_ID,
        snapshot_id=SNAPSHOT_ID,
        model_version_id=MODEL_ID,
        lambda_base={"home": 1.2, "away": 0.9},
        lambda_adjusted={"home": 1.3, "away": 0.8},
        probabilities={
            "home": 33.333333,
            "draw": 33.333333,
            "away": 33.333333,
            "next_goal_home": 62.5,
        },
        explanation={},
        quality="fresh",
    )

    assert prediction.probabilities["next_goal_home"] == 62.5


def test_probability_total_rejects_noncanonical_one_x_two_sum():
    with pytest.raises(ValidationError):
        PredictionRecord(
            fixture_id=FIXTURE_ID,
            snapshot_id=SNAPSHOT_ID,
            model_version_id=MODEL_ID,
            lambda_base={"home": 1.2, "away": 0.9},
            lambda_adjusted={"home": 1.3, "away": 0.8},
            probabilities={"home": 40.0, "draw": 30.0, "away": 29.5},
            explanation={},
            quality="fresh",
        )


@pytest.mark.parametrize("field", ["brier", "log_loss"])
def test_model_metrics_are_nonnegative_and_datetimes_are_aware(field, model_row):
    invalid = model_row | {field: -0.01}
    with pytest.raises(ValidationError):
        ModelVersion.model_validate(invalid)

    invalid = model_row | {"created_at": "2026-09-01T12:00:00"}
    with pytest.raises(ValidationError):
        ModelVersion.model_validate(invalid)


@pytest.mark.parametrize("field", ["brier", "log_loss"])
@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_model_metrics_reject_non_finite_values(field, non_finite, model_row):
    with pytest.raises(ValidationError):
        ModelVersion.model_validate(model_row | {field: non_finite})


def test_repository_is_a_protocol():
    assert Repository._is_protocol is True
    assert list(inspect.signature(Repository.claim_job).parameters) == [
        "self",
        "name",
        "key",
        "lease_seconds",
    ]


def test_job_claim_is_strict_and_uses_uuid_fencing_fields():
    claim = domain.JobClaim(
        run_id=str(RUN_ID),
        request_id=str(FENCING_REQUEST_ID),
    )
    assert claim.run_id == RUN_ID
    assert claim.request_id == FENCING_REQUEST_ID

    with pytest.raises(ValidationError):
        domain.JobClaim(
            run_id=RUN_ID,
            request_id=FENCING_REQUEST_ID,
            http_request_id=HTTP_REQUEST_ID,
        )


def test_gateway_creates_one_server_client_from_settings(fake_client):
    calls = []
    settings = Settings(
        environment="test",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="server-secret",
        cron_secret="cron-secret",
    )

    gateway = SupabaseGateway(
        settings,
        client_factory=lambda url, key: calls.append((url, key)) or fake_client,
    )

    assert gateway is not None
    assert calls == [("https://example.supabase.co/", "server-secret")]


def test_fixture_upsert_uses_private_schema_conflict_and_canonical_payload(
    gateway, fake_client, fixture
):
    fake_client.responses[("private", "table", "fixtures")] = [{"id": str(FIXTURE_ID)}]

    assert gateway.upsert_fixtures([fixture]) == 1

    operation = fake_client.operations[-1]
    assert operation["schema"] == "private"
    assert operation["name"] == "fixtures"
    assert operation["on_conflict"] == "provider,provider_fixture_id"
    assert operation["payload"] == [
        {
            "provider": "365scores",
            "provider_fixture_id": "provider-42",
            "competition": "Premier League",
            "home_name": "Arsenal",
            "away_name": "Chelsea",
            "home_logo_url": "https://cdn.example/arsenal.png",
            "away_logo_url": None,
            "scheduled_at": "2026-09-24T20:00:00Z",
            "status": "live",
            "final_home": None,
            "final_away": None,
            "finished_at": None,
        }
    ]


def test_empty_fixture_upsert_does_not_create_an_operation(gateway, fake_client):
    assert gateway.upsert_fixtures([]) == 0
    assert fake_client.operations == []


def test_active_fixtures_uses_private_schema_bounded_select_and_limit(
    gateway, fake_client, fixture
):
    fake_client.responses[("private", "table", "fixtures")] = [
        fixture.model_dump(mode="json")
    ]

    assert gateway.active_fixtures(limit=500) == [fixture]

    operation = fake_client.operations[-1]
    assert operation == {
        "schema": "private",
        "kind": "table",
        "name": "fixtures",
        "params": None,
        "select": (
            "id,public_id,provider,provider_fixture_id,competition,home_name,"
            "away_name,home_logo_url,away_logo_url,scheduled_at,status,final_home,"
            "final_away,discovered_at,updated_at,finished_at"
        ),
        "filters": [("eq", "status", "live")],
        "limit": 100,
    }


def test_snapshot_write_uses_private_schema_and_keeps_observation_precision(
    gateway, fake_client, snapshot
):
    fake_client.responses[("private", "table", "live_snapshots")] = [
        {"id": str(SNAPSHOT_ID)}
    ]

    assert gateway.store_snapshot(snapshot) == SNAPSHOT_ID
    assert gateway.store_snapshot(snapshot) == SNAPSHOT_ID

    first_operation, operation = fake_client.operations[-2:]
    assert first_operation["on_conflict"] == "fixture_id,observation_bucket"
    assert operation["on_conflict"] == "fixture_id,observation_bucket"
    assert operation["schema"] == "private"
    assert operation["payload"] == {
        "fixture_id": str(FIXTURE_ID),
        "minute": 63,
        "score_home": 1,
        "score_away": 1,
        "normalized_stats": {"shots_on_target": {"home": 4, "away": None}},
        "sanitized_provider_data": {"clock": "63:44"},
        "provider_observed_at": "2026-09-24T20:03:44Z",
        "quality": "degraded",
        "observation_bucket": "2026-09-24T20:03:00Z",
    }


def test_prediction_write_uses_only_canonical_private_payload(
    gateway, fake_client, prediction
):
    prediction_id = UUID("f03e8a72-b2ec-4b0d-899f-17736aef45e4")
    fake_client.responses[("private", "table", "predictions")] = [
        {"id": str(prediction_id)}
    ]

    assert gateway.store_prediction(prediction) == prediction_id
    assert gateway.store_prediction(prediction) == prediction_id

    first_operation, operation = fake_client.operations[-2:]
    assert first_operation["on_conflict"] == "snapshot_id,model_version_id"
    assert operation["on_conflict"] == "snapshot_id,model_version_id"
    assert operation == {
        "schema": "private",
        "kind": "table",
        "name": "predictions",
        "params": None,
        "payload": {
            "fixture_id": str(FIXTURE_ID),
            "snapshot_id": str(SNAPSHOT_ID),
            "model_version_id": str(MODEL_ID),
            "lambda_base": {"home": 1.2, "away": 0.9},
            "lambda_adjusted": {"home": 1.3, "away": 0.8},
            "probabilities": {"home": 45.0, "draw": 30.0, "away": 25.0},
            "explanation": {"signals": ["shots_on_target"]},
            "quality": "fresh",
        },
        "on_conflict": "snapshot_id,model_version_id",
        "select": "id",
        "limit": 1,
    }


def test_active_model_uses_private_schema_and_returns_strict_model(
    gateway, fake_client, model_row
):
    fake_client.responses[("private", "table", "model_versions")] = [model_row]

    model = gateway.active_model()

    assert model.id == MODEL_ID
    assert model.state == "active"
    assert fake_client.operations[-1] == {
        "schema": "private",
        "kind": "table",
        "name": "model_versions",
        "params": None,
        "select": (
            "id,version,state,parameters,parameter_hash,code_version,"
            "previous_model_id,train_size,validation_size,brier,log_loss,"
            "created_at,activated_at"
        ),
        "filters": [("eq", "state", "active")],
        "limit": 1,
    }


def test_public_live_uses_public_schema_bounded_columns_and_caps_limit(
    gateway, fake_client
):
    row = {
        "public_id": "337a09f3-a806-4e56-a068-d758f74a78cb",
        "competition": "Premier League",
    }
    fake_client.responses[("public", "table", "live_matches")] = [row]

    assert gateway.public_live(limit=500, cursor=None) == [row]

    operation = fake_client.operations[-1]
    assert operation["schema"] == "public"
    assert operation["name"] == "live_matches"
    assert operation["limit"] == 50
    assert operation["select"] != "*"
    assert "sanitized_provider_data" not in operation["select"]
    assert "provider_fixture_id" not in operation["select"]


def test_public_live_applies_cursor_and_clamps_nonpositive_limit(
    gateway, fake_client
):
    gateway.public_live(limit=0, cursor="2026-09-24T20:03:00Z")

    operation = fake_client.operations[-1]
    assert operation["schema"] == "public"
    assert operation["filters"] == [
        ("lt", "updated_at", "2026-09-24T20:03:00Z")
    ]
    assert operation["order"] == ("updated_at", True)
    assert operation["limit"] == 1


def test_public_match_uses_safe_public_projection(gateway, fake_client):
    public_id = UUID("337a09f3-a806-4e56-a068-d758f74a78cb")
    row = {"public_id": str(public_id), "competition": "Premier League"}
    fake_client.responses[("public", "table", "live_matches")] = [row]

    assert gateway.public_match(public_id) == row

    operation = fake_client.operations[-1]
    assert operation["schema"] == "public"
    assert operation["name"] == "live_matches"
    assert operation["filters"] == [("eq", "public_id", str(public_id))]
    assert operation["limit"] == 1
    assert operation["select"] != "*"
    assert "provider_fixture_id" not in operation["select"]


def test_public_model_status_uses_safe_public_projection(gateway, fake_client):
    row = {"version": "2026.09.24.1", "train_size": 70}
    fake_client.responses[("public", "table", "model_status")] = [row]

    assert gateway.public_model_status() == row

    operation = fake_client.operations[-1]
    assert operation == {
        "schema": "public",
        "kind": "table",
        "name": "model_status",
        "params": None,
        "select": (
            "version,train_size,validation_size,brier,log_loss,activated_at,"
            "last_training_finished_at,last_training_decision,updated_at"
        ),
        "limit": 1,
    }


def test_job_rpcs_use_private_schema_and_exact_fencing_parameters(
    gateway, fake_client
):
    fake_client.responses[("private", "rpc", "claim_job")] = [
        {"id": str(RUN_ID), "request_id": str(FENCING_REQUEST_ID)}
    ]

    claimed = gateway.claim_job("collect", "collect:2026-09-24T20:03Z", 120)
    assert isinstance(claimed, domain.JobClaim)
    assert claimed.run_id == RUN_ID
    assert claimed.request_id == FENCING_REQUEST_ID
    assert claimed.request_id != HTTP_REQUEST_ID

    gateway.finish_job(
        claimed.run_id,
        claimed.request_id,
        "failed",
        {"processed": 2},
        "provider_unavailable",
    )

    claim, finish = fake_client.operations[-2:]
    assert claim == {
        "schema": "private",
        "kind": "rpc",
        "name": "claim_job",
        "params": {
            "p_job_name": "collect",
            "p_idempotency_key": "collect:2026-09-24T20:03Z",
            "p_lease_seconds": 120,
        },
    }
    assert finish == {
        "schema": "private",
        "kind": "rpc",
        "name": "finish_job",
        "params": {
            "p_run_id": str(RUN_ID),
            "p_request_id": str(FENCING_REQUEST_ID),
            "p_state": "failed",
            "p_counters": {"processed": 2},
            "p_sanitized_error": "provider_unavailable",
        },
    }


def test_transport_failure_is_sanitized_and_chained(gateway, fake_client):
    transport_error = RuntimeError("raw database response with secret")
    fake_client.failure = transport_error

    with pytest.raises(RepositoryUnavailable) as captured:
        gateway.public_live(limit=12, cursor=None)

    assert str(captured.value) == "El repositorio no está disponible temporalmente."
    assert "secret" not in str(captured.value)
    assert captured.value.__cause__ is transport_error


def test_client_creation_failure_is_sanitized_and_chained():
    settings = Settings(
        environment="test",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="server-secret",
        cron_secret="cron-secret",
    )
    transport_error = RuntimeError("raw client error with server-secret")

    def fail_to_create(url, key):
        raise transport_error

    with pytest.raises(RepositoryUnavailable) as captured:
        SupabaseGateway(settings, client_factory=fail_to_create)

    assert str(captured.value) == "El repositorio no está disponible temporalmente."
    assert "server-secret" not in str(captured.value)
    assert captured.value.__cause__ is transport_error
