from datetime import datetime, timezone
import inspect
from uuid import UUID

import pytest
from pydantic import ValidationError

from football_live import domain
from football_live.domain import (
    Fixture,
    LiveSnapshot,
    MatchEvent,
    ModelVersion,
    PredictionRecord,
)
from football_live.repository import Repository, RepositoryUnavailable
from football_live.settings import Settings
from football_live.supabase_gateway import SupabaseGateway
from football_live.training import (
    CandidateEvaluation,
    TrainingExample,
    TrainingMetrics,
)


FIXTURE_ID = UUID("4b1c7cb7-7ce5-4fc4-bf89-4744c80a31b1")
SNAPSHOT_ID = UUID("22b244f7-771a-4ea3-953d-bfd828743a45")
MODEL_ID = UUID("bb9ad924-88d5-4b87-9af4-143c3bda250e")
CURRENT_MODEL_ID = UUID("10000000-0000-0000-0000-000000000001")
RUN_ID = UUID("3746b572-e1fe-4131-a25b-7e33c92d8efe")
FENCING_REQUEST_ID = UUID("8d69f642-7623-4779-bebd-85d6fc167553")
HTTP_REQUEST_ID = UUID("cec47ae5-66e1-445d-9073-78560b471e42")
OBSERVED_AT = datetime(2026, 9, 24, 20, 3, 44, tzinfo=timezone.utc)
MODEL_CREATED_AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
HISTORY_SNAPSHOT_IDS = (UUID(int=101), UUID(int=102), UUID(int=103))


class FakeResponse:
    def __init__(self, data=None):
        self.data = [] if data is None else data


class FakeWriteSelectQuery:
    """Mirror postgrest-py's write-select builder, which has no limit()."""

    def __init__(self, query):
        self.query = query

    def execute(self):
        return self.query.execute()


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
        if "payload" in self.operation:
            return FakeWriteSelectQuery(self)
        return self

    def upsert(self, payload, *, on_conflict):
        self.operation["payload"] = payload
        self.operation["on_conflict"] = on_conflict
        return self

    def insert(self, payload):
        self.operation["payload"] = payload
        return self

    def update(self, payload):
        self.operation["payload"] = payload
        return self

    def eq(self, column, value):
        self.operation.setdefault("filters", []).append(("eq", column, value))
        return self

    def lt(self, column, value):
        self.operation.setdefault("filters", []).append(("lt", column, value))
        return self

    def is_(self, column, value):
        self.operation.setdefault("filters", []).append(("is", column, value))
        return self

    def order(self, column, *, desc=False):
        self.operation["order"] = (column, desc)
        return self

    def limit(self, value):
        self.operation["limit"] = value
        return self

    def range(self, start, end):
        self.operation["range"] = (start, end)
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
        if key in self.client.table_rows:
            rows = self.client.table_rows[key]
            start, requested_end = self.operation.get("range", (0, len(rows) - 1))
            capped_end = min(
                requested_end,
                start + self.client.server_max_rows - 1,
            )
            return FakeResponse(rows[start : capped_end + 1])
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
        self.table_rows = {}
        self.server_max_rows = 1000
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


def history_snapshot_rows():
    return [
        {
            "id": str(HISTORY_SNAPSHOT_IDS[2]),
            "minute": 62,
            "score_home": 1,
            "score_away": 0,
            "normalized_stats": {
                "possession": {"home": 54.0, "away": 46.0},
                "shots": {"home": 8, "away": 5},
                "shots_on_target": {"home": 4, "away": 2},
                "provider_player_id": "provider-player-raw",
            },
            "provider_observed_at": "2026-09-24T20:02:00Z",
            "collected_at": "2026-09-24T20:02:02Z",
            "quality": "fresh",
            "sanitized_provider_data": {"provider_fixture_id": "raw-42"},
        },
        {
            "id": str(HISTORY_SNAPSHOT_IDS[1]),
            "minute": 61,
            "score_home": 0,
            "score_away": 0,
            "normalized_stats": {
                "shots": {"home": 7},
                "expected_goals": {"away": 0.3},
            },
            "provider_observed_at": "2026-09-24T20:01:00Z",
            "collected_at": None,
            "quality": "degraded",
        },
        {
            "id": str(HISTORY_SNAPSHOT_IDS[0]),
            "minute": 60,
            "score_home": 0,
            "score_away": 0,
            "normalized_stats": {},
            "provider_observed_at": "2026-09-24T20:00:00Z",
            "collected_at": "2026-09-24T20:00:02Z",
            "quality": "fresh",
        },
    ]


def history_prediction_rows():
    return [
        {
            "snapshot_id": str(HISTORY_SNAPSHOT_IDS[2]),
            "probabilities": {
                "home": 45.0,
                "draw": 30.0,
                "away": 25.0,
                "provider_prediction_id": "raw-prediction",
            },
            "lambda_adjusted": {"home": 1.3, "away": 0.8, "internal": 99},
            "created_at": "2026-09-24T20:02:20Z",
            "model_version": "active-v1",
            "model_version_id": str(MODEL_ID),
            "parameters": {"secret": True},
        },
        {
            "snapshot_id": str(HISTORY_SNAPSHOT_IDS[1]),
            "probabilities": {"home": 35.0, "draw": 35.0, "away": 30.0},
            "lambda_adjusted": {"home": 1.0, "away": 0.9},
            "created_at": "2026-09-24T20:01:40Z",
            "model_version": "fallback-newest",
        },
        {
            "snapshot_id": str(HISTORY_SNAPSHOT_IDS[0]),
            "probabilities": {"home": 33.0, "draw": 34.0, "away": 33.0},
            "lambda_adjusted": {"home": 0.8, "away": 0.7},
            "created_at": "2026-09-24T20:00:30Z",
            "model_version": "active-v1",
        },
    ]


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
    assert "limit" not in operation
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


def test_store_match_events_upserts_only_canonical_fields_by_fixture_and_order(
    gateway, fake_client, fixture
):
    events = [
        MatchEvent(
            provider_order=2,
            minute=45,
            added_time=3,
            side="away",
            kind="yellow_card",
        )
    ]
    fake_client.responses[("private", "table", "match_events")] = [{}]

    assert gateway.store_match_events(fixture, events) == 1

    operation = fake_client.operations[-1]
    assert operation["schema"] == "private"
    assert operation["name"] == "match_events"
    assert operation["on_conflict"] == "fixture_id,provider_event_order"
    assert operation["payload"] == [
        {
            "fixture_id": str(FIXTURE_ID),
            "provider_event_order": 2,
            "minute": 45,
            "added_time": 3,
            "side": "away",
            "kind": "yellow_card",
        }
    ]


def test_store_match_events_skips_empty_events_without_a_database_write(
    gateway, fake_client, fixture
):
    assert gateway.store_match_events(fixture, ()) == 0
    assert fake_client.operations == []


def test_store_match_events_rejects_an_unpersisted_fixture(gateway, fake_client, fixture):
    event = MatchEvent(provider_order=1, minute=1, side="home", kind="goal")

    with pytest.raises(ValueError, match="persisted fixture is required"):
        gateway.store_match_events(fixture.model_copy(update={"id": None}), [event])

    assert fake_client.operations == []


def test_store_match_events_reuses_the_fixture_and_order_conflict_key(
    gateway, fake_client, fixture
):
    event = MatchEvent(provider_order=1, minute=74, side="away", kind="goal")
    fake_client.responses[("private", "table", "match_events")] = [{}]

    assert gateway.store_match_events(fixture, [event]) == 1
    assert gateway.store_match_events(fixture, [event]) == 1

    assert [operation["on_conflict"] for operation in fake_client.operations] == [
        "fixture_id,provider_event_order",
        "fixture_id,provider_event_order",
    ]


def test_sync_live_fixture_logos_updates_only_public_logo_columns(
    gateway, fake_client, fixture
):
    gateway.sync_live_fixture_logos(fixture)

    assert fake_client.operations[-1] == {
        "schema": "public",
        "kind": "table",
        "name": "live_match_projection",
        "params": None,
        "payload": {
            "home_logo_url": fixture.home_logo_url,
            "away_logo_url": fixture.away_logo_url,
        },
        "filters": [("eq", "public_id", str(fixture.public_id))],
    }


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
        "limit": 12,
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
    }


def test_live_prediction_publication_uses_only_the_safe_projection(
    gateway, fake_client, fixture, snapshot, prediction, model_row
):
    model = ModelVersion.model_validate(model_row)

    gateway.publish_live_prediction(fixture, snapshot, prediction, model)

    operation = fake_client.operations[-1]
    assert operation == {
        "schema": "public",
        "kind": "table",
        "name": "live_match_projection",
        "params": None,
        "payload": {
            "public_id": str(fixture.public_id),
            "competition": "Premier League",
            "home_name": "Arsenal",
            "away_name": "Chelsea",
            "home_logo_url": "https://cdn.example/arsenal.png",
            "away_logo_url": None,
            "scheduled_at": "2026-09-24T20:00:00Z",
            "status": "live",
            "minute": 63,
            "score_home": 1,
            "score_away": 1,
            "data_status": "fresh",
            "provider_observed_at": "2026-09-24T20:03:44Z",
            "collected_at": None,
            "probabilities": {"home": 45.0, "draw": 30.0, "away": 25.0},
            "explanation": {"signals": ["shots_on_target"]},
            "model_version": "2026.09.24.1",
            "prediction_created_at": None,
            "updated_at": "2026-09-24T20:03:44Z",
        },
        "on_conflict": "public_id",
    }
    assert "provider_fixture_id" not in operation["payload"]
    assert "sanitized_provider_data" not in operation["payload"]


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
    assert operation["filters"] == [("eq", "status", "live")]
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
        ("eq", "status", "live"),
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


def test_public_match_history_uses_private_allowlist_and_returns_chronological_points(
    gateway, fake_client
):
    public_id = UUID("337a09f3-a806-4e56-a068-d758f74a78cb")
    fake_client.responses[("private", "table", "fixtures")] = [
        {
            "id": str(FIXTURE_ID),
            "public_id": str(public_id),
            "home_name": "Arsenal",
            "away_name": "Chelsea",
            "provider_fixture_id": "provider-fixture-raw",
        }
    ]
    fake_client.responses[("private", "table", "live_snapshots")] = (
        history_snapshot_rows()
    )
    fake_client.responses[("private", "rpc", "match_history_predictions")] = (
        history_prediction_rows()
    )

    result = gateway.public_match_history(public_id, limit=500)

    assert result is not None
    assert set(result) == {
        "public_id",
        "home_name",
        "away_name",
        "model_version",
        "points",
    }
    assert [point["minute"] for point in result["points"]] == [60, 61, 62]
    assert result["model_version"] == "active-v1"
    assert result["points"][-1]["probabilities"] == {
        "home": 45.0,
        "draw": 30.0,
        "away": 25.0,
    }
    assert result["points"][1]["probabilities"] == {
        "home": 35.0,
        "draw": 35.0,
        "away": 30.0,
    }
    assert result["points"][-1]["stats"]["shots_on_target"] == {
        "home": 4,
        "away": 2,
    }
    assert result["points"][1]["stats"]["shots"] == {
        "home": 7,
        "away": None,
    }
    assert result["points"][0]["stats"] == {
        key: {"home": None, "away": None}
        for key in (
            "possession",
            "shots",
            "shots_on_target",
            "corners",
            "yellow_cards",
            "red_cards",
            "expected_goals",
        )
    }
    assert set(result["points"][-1]) == {
        "minute",
        "score_home",
        "score_away",
        "stats",
        "provider_observed_at",
        "collected_at",
        "quality",
        "probabilities",
        "lambda_adjusted",
        "prediction_created_at",
    }
    serialized = str(result)
    for forbidden in (
        str(FIXTURE_ID),
        *(str(item) for item in HISTORY_SNAPSHOT_IDS),
        str(MODEL_ID),
        "provider-fixture-raw",
        "provider-player-raw",
        "raw-prediction",
        "sanitized_provider_data",
        "parameters",
        "secret",
    ):
        assert forbidden not in serialized

    fixture_query, snapshot_query, prediction_query = fake_client.operations[-3:]
    assert fixture_query == {
        "schema": "private",
        "kind": "table",
        "name": "fixtures",
        "params": None,
        "select": "id,public_id,home_name,away_name",
        "filters": [("eq", "public_id", str(result["public_id"]))],
        "limit": 1,
    }
    assert snapshot_query == {
        "schema": "private",
        "kind": "table",
        "name": "live_snapshots",
        "params": None,
        "select": (
            "id,minute,score_home,score_away,normalized_stats,"
            "provider_observed_at,collected_at,quality"
        ),
        "filters": [("eq", "fixture_id", str(FIXTURE_ID))],
        "order": ("provider_observed_at", True),
        "limit": 90,
    }
    assert prediction_query == {
        "schema": "private",
        "kind": "rpc",
        "name": "match_history_predictions",
        "params": {"p_fixture_id": str(FIXTURE_ID), "p_limit": 90},
    }


def test_public_match_history_returns_none_without_private_history_queries(
    gateway, fake_client
):
    public_id = UUID("337a09f3-a806-4e56-a068-d758f74a78cb")

    assert gateway.public_match_history(public_id) is None
    assert len(fake_client.operations) == 1
    assert fake_client.operations[0]["name"] == "fixtures"


@pytest.mark.parametrize(
    "malformed_stats",
    [[], {"shots": 4}],
)
def test_public_match_history_rejects_malformed_stat_payloads(
    gateway, fake_client, malformed_stats
):
    public_id = UUID("337a09f3-a806-4e56-a068-d758f74a78cb")
    fake_client.responses[("private", "table", "fixtures")] = [
        {
            "id": str(FIXTURE_ID),
            "public_id": str(public_id),
            "home_name": "Arsenal",
            "away_name": "Chelsea",
        }
    ]
    snapshot_row = history_snapshot_rows()[0]
    snapshot_row["normalized_stats"] = malformed_stats
    fake_client.responses[("private", "table", "live_snapshots")] = [snapshot_row]
    fake_client.responses[("private", "rpc", "match_history_predictions")] = []

    with pytest.raises(RepositoryUnavailable) as captured:
        gateway.public_match_history(public_id)

    assert str(captured.value) == "El repositorio no está disponible temporalmente."


@pytest.mark.parametrize(
    "malformed_leaf",
    [
        True,
        "4",
        [],
        {"secret_marker": "nested-secret-value"},
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_public_match_history_rejects_nonfinite_or_nonnumeric_stat_leaves(
    gateway, fake_client, malformed_leaf
):
    public_id = UUID("337a09f3-a806-4e56-a068-d758f74a78cb")
    fake_client.responses[("private", "table", "fixtures")] = [
        {
            "id": str(FIXTURE_ID),
            "public_id": str(public_id),
            "home_name": "Arsenal",
            "away_name": "Chelsea",
        }
    ]
    snapshot_row = history_snapshot_rows()[0]
    snapshot_row["normalized_stats"]["shots"]["home"] = malformed_leaf
    fake_client.responses[("private", "table", "live_snapshots")] = [snapshot_row]
    fake_client.responses[("private", "rpc", "match_history_predictions")] = []

    with pytest.raises(RepositoryUnavailable) as captured:
        gateway.public_match_history(public_id)

    assert str(captured.value) == "El repositorio no está disponible temporalmente."
    assert "nested-secret-value" not in str(captured.value)
    assert "nested-secret-value" not in repr(captured.value.__cause__)


def test_public_match_history_sanitizes_oversized_integer_stat_leaf(
    gateway, fake_client
):
    public_id = UUID("337a09f3-a806-4e56-a068-d758f74a78cb")
    secret_marker = "oversized-integer-secret-value"
    fake_client.responses[("private", "table", "fixtures")] = [
        {
            "id": str(FIXTURE_ID),
            "public_id": str(public_id),
            "home_name": "Arsenal",
            "away_name": "Chelsea",
        }
    ]
    snapshot_row = history_snapshot_rows()[0]
    snapshot_row["normalized_stats"]["shots"] = {
        "home": 10**10000,
        "away": 2,
        "secret_marker": secret_marker,
    }
    fake_client.responses[("private", "table", "live_snapshots")] = [snapshot_row]
    fake_client.responses[("private", "rpc", "match_history_predictions")] = []

    with pytest.raises(RepositoryUnavailable) as captured:
        gateway.public_match_history(public_id)

    assert str(captured.value) == "El repositorio no está disponible temporalmente."
    error_chain = []
    error = captured.value
    while error is not None:
        error_chain.append(str(error))
        error = error.__cause__
    sanitized_errors = " ".join(error_chain)
    assert secret_marker not in sanitized_errors
    assert "10000000000000000000" not in sanitized_errors


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


def test_unfinished_fixtures_are_bounded_and_selected_from_private_schema(
    gateway, fake_client, fixture
):
    fake_client.responses[("private", "table", "fixtures")] = [
        fixture.model_dump(mode="json")
    ]

    assert gateway.unfinished_fixtures(limit=500) == [fixture]

    assert fake_client.operations[-1] == {
        "schema": "private",
        "kind": "table",
        "name": "fixtures",
        "params": None,
        "select": (
            "id,public_id,provider,provider_fixture_id,competition,home_name,away_name,"
            "home_logo_url,away_logo_url,scheduled_at,status,final_home,final_away,"
            "discovered_at,updated_at,finished_at"
        ),
        "filters": [("is", "finished_at", "null")],
        "order": ("scheduled_at", False),
        "limit": 30,
    }


def test_confirmed_outcome_is_upserted_before_fixture_is_closed(
    gateway, fake_client, fixture
):
    fake_client.responses[("private", "table", "outcomes")] = [
        {"fixture_id": str(FIXTURE_ID)}
    ]
    fake_client.responses[("private", "table", "fixtures")] = [
        {"id": str(FIXTURE_ID)}
    ]

    gateway.store_confirmed_outcome(
        fixture,
        home_score=2,
        away_score=1,
        confirmed_at=OBSERVED_AT,
        source="365scores",
    )

    outcome, closed_fixture = fake_client.operations[-2:]
    assert outcome == {
        "schema": "private",
        "kind": "table",
        "name": "outcomes",
        "params": None,
        "payload": {
            "fixture_id": str(FIXTURE_ID),
            "home_score": 2,
            "away_score": 1,
            "source": "365scores",
            "confirmed": True,
            "confirmed_at": OBSERVED_AT.isoformat(),
        },
        "on_conflict": "fixture_id",
        "select": "fixture_id",
    }
    assert closed_fixture["name"] == "fixtures"
    assert closed_fixture["payload"][0]["status"] == "finished"
    assert closed_fixture["payload"][0]["final_home"] == 2
    assert closed_fixture["payload"][0]["final_away"] == 1
    assert closed_fixture["payload"][0]["finished_at"] == "2026-09-24T20:03:44Z"


def test_disputed_correction_marks_fixture_for_review_without_writing_outcome(
    gateway, fake_client, fixture
):
    fake_client.responses[("private", "table", "fixtures")] = [
        {"id": str(FIXTURE_ID)}
    ]

    gateway.flag_fixture_review(fixture, "provider_score_correction")

    operation = fake_client.operations[-1]
    assert operation["name"] == "fixtures"
    assert operation["payload"][0]["status"] == "review"
    assert operation["payload"][0]["final_home"] is None
    assert operation["payload"][0]["finished_at"] is None
    assert all(item["name"] != "outcomes" for item in fake_client.operations)


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


def test_training_examples_use_private_bounded_explicit_projection(gateway, fake_client):
    fixture_id = UUID(int=1)
    fake_client.responses[("private", "table", "training_examples")] = [
        {
            "fixture_id": str(fixture_id),
            "first_observed_at": "2026-01-01T10:00:00Z",
            "outcome_confirmed_at": "2026-01-01T12:00:00Z",
            "final_home": 2,
            "final_away": 0,
            "observations": [
                {
                    "observed_at": "2026-01-01T10:00:00Z",
                    "minute": 60,
                    "score_home": 0,
                    "score_away": 0,
                    "lambda_base": {"home": 0.8, "away": 0.2},
                }
            ],
        }
    ]

    examples = gateway.training_examples(limit=50000)

    assert examples == [TrainingExample.model_validate(fake_client.responses[("private", "table", "training_examples")][0])]
    operation = fake_client.operations[-1]
    assert operation == {
        "schema": "private",
        "kind": "table",
        "name": "training_examples",
        "params": None,
        "select": (
            "fixture_id,first_observed_at,outcome_confirmed_at,final_home,"
            "final_away,observations"
        ),
        "order": ("first_observed_at", True),
        "limit": 1000,
    }


def test_consumed_validation_ids_use_service_only_bounded_view(gateway, fake_client):
    ids = [UUID(int=1), UUID(int=2)]
    fake_client.table_rows[("private", "table", "consumed_validation_fixtures")] = [
        {"fixture_id": str(item)} for item in ids
    ]

    assert gateway.consumed_validation_ids() == set(ids)
    assert fake_client.operations[-1] == {
        "schema": "private",
        "kind": "table",
        "name": "consumed_validation_fixtures",
        "params": None,
        "select": "fixture_id",
        "order": ("fixture_id", False),
        "range": (0, 999),
    }


def test_consumed_validation_ids_pages_past_exact_server_cap_multiple(
    gateway, fake_client
):
    rows = [
        {"fixture_id": str(UUID(int=index))} for index in range(1, 2001)
    ]
    fake_client.table_rows[
        ("private", "table", "consumed_validation_fixtures")
    ] = rows

    assert gateway.consumed_validation_ids(limit=2000) == {
        UUID(row["fixture_id"]) for row in rows
    }
    assert [operation["range"] for operation in fake_client.operations[-3:]] == [
        (0, 999),
        (1000, 1999),
        (2000, 2000),
    ]


def test_consumed_validation_ids_fail_closed_past_total_bound(gateway, fake_client):
    fake_client.table_rows[
        ("private", "table", "consumed_validation_fixtures")
    ] = [
        {"fixture_id": str(UUID(int=index))} for index in range(1, 2002)
    ]

    with pytest.raises(RepositoryUnavailable):
        gateway.consumed_validation_ids(limit=2000)

    assert fake_client.operations[-1]["range"] == (2000, 2000)


def test_consumed_validation_ids_never_read_past_fifty_thousand(
    gateway, fake_client
):
    fake_client.table_rows[
        ("private", "table", "consumed_validation_fixtures")
    ] = [
        {"fixture_id": str(UUID(int=index))} for index in range(1, 50002)
    ]

    with pytest.raises(RepositoryUnavailable):
        gateway.consumed_validation_ids(limit=99999)

    ranges = [operation["range"] for operation in fake_client.operations]
    assert ranges[0] == (0, 999)
    assert ranges[-2:] == [(49000, 49999), (50000, 50000)]
    assert all(end - start + 1 <= 1000 for start, end in ranges)


def test_training_evidence_and_promotion_use_exact_private_rpcs(
    gateway, fake_client, model_row
):
    train_ids = tuple(UUID(int=index) for index in range(1, 71))
    validation_ids = tuple(UUID(int=index) for index in range(71, 101))
    evaluation = CandidateEvaluation(
        version="live-fit-abc123",
        parameters={"factores": {"local": 1.1, "visitante": 0.9}},
        parameter_hash="a" * 64,
        code_version="abc123",
        previous_model_id=MODEL_ID,
        train_fixture_ids=train_ids,
        validation_fixture_ids=validation_ids,
        candidate_metrics=TrainingMetrics(brier=0.18, log_loss=0.58),
        champion_metrics=TrainingMetrics(brier=0.19, log_loss=0.61),
        baseline_metrics=TrainingMetrics(brier=0.21, log_loss=0.65),
        approved=True,
        reason="candidate strictly improved both metrics",
    )
    fake_client.responses[("private", "rpc", "record_training_evaluation")] = [
        {
            "id": str(RUN_ID),
            "candidate_model_id": str(MODEL_ID),
            "decision": "running",
            "parameter_hash": "a" * 64,
        }
    ]
    fake_client.responses[("private", "rpc", "promote_model")] = [model_row]

    recorded = gateway.record_training_evaluation(evaluation, RUN_ID)
    promoted = gateway.promote_model(MODEL_ID, CURRENT_MODEL_ID)

    assert recorded.id == RUN_ID
    assert recorded.status == "candidate"
    assert promoted.id == MODEL_ID
    record_operation, promote_operation = fake_client.operations[-2:]
    assert record_operation["schema"] == "private"
    assert record_operation["name"] == "record_training_evaluation"
    assert record_operation["params"] == {
        "p_run_id": str(RUN_ID),
        "p_parameters": evaluation.parameters,
        "p_code_version": evaluation.code_version,
        "p_previous_model_id": str(MODEL_ID),
        "p_train_fixture_ids": [str(item) for item in train_ids],
        "p_validation_fixture_ids": [str(item) for item in validation_ids],
        "p_metrics": {
            "candidate": {"brier": 0.18, "log_loss": 0.58},
            "champion": {"brier": 0.19, "log_loss": 0.61},
            "baseline": {"brier": 0.21, "log_loss": 0.65},
        },
        "p_approved": True,
        "p_reason": "candidate strictly improved both metrics",
    }
    assert promote_operation == {
        "schema": "private",
        "kind": "rpc",
        "name": "promote_model",
        "params": {
            "p_candidate_id": str(MODEL_ID),
            "p_current_id": str(CURRENT_MODEL_ID),
        },
    }


def test_training_recovery_rpcs_map_terminal_state_and_use_safe_bounds(
    gateway, fake_client
):
    fake_client.responses[("private", "rpc", "finalize_training_evaluation")] = [
        {
            "id": str(RUN_ID),
            "candidate_model_id": str(MODEL_ID),
            "decision": "succeeded",
            "parameter_hash": "b" * 64,
            "train_fixture_ids": [str(UUID(int=21))],
            "validation_fixture_ids": [str(UUID(int=22))],
        }
    ]
    fake_client.responses[
        ("private", "rpc", "recover_abandoned_training_evaluations")
    ] = [
        {
            "id": str(UUID(int=11)),
            "candidate_model_id": str(UUID(int=12)),
            "decision": "failed",
            "parameter_hash": "c" * 64,
        }
    ]

    finalized = gateway.finalize_training_evaluation(RUN_ID)
    recovered = gateway.recover_abandoned_training_evaluations(
        stale_after_seconds=900,
        limit=100,
    )

    assert finalized.status == "promoted"
    assert finalized.active_model_id == MODEL_ID
    assert finalized.train_fixture_ids == (UUID(int=21),)
    assert finalized.validation_fixture_ids == (UUID(int=22),)
    assert recovered == 1
    finalize_operation, recover_operation = fake_client.operations[-2:]
    assert finalize_operation == {
        "schema": "private",
        "kind": "rpc",
        "name": "finalize_training_evaluation",
        "params": {"p_run_id": str(RUN_ID)},
    }
    assert recover_operation == {
        "schema": "private",
        "kind": "rpc",
        "name": "recover_abandoned_training_evaluations",
        "params": {"p_stale_after_seconds": 900, "p_limit": 100},
    }


def test_record_collision_can_return_a_terminal_run_without_a_candidate(
    gateway, fake_client, model_row
):
    train_ids = tuple(UUID(int=index) for index in range(1, 71))
    validation_ids = tuple(UUID(int=index) for index in range(71, 101))
    evaluation = CandidateEvaluation(
        version="live-fit-abc123",
        parameters={"factores": {"local": 1.1, "visitante": 0.9}},
        parameter_hash="a" * 64,
        code_version="abc123",
        previous_model_id=CURRENT_MODEL_ID,
        train_fixture_ids=train_ids,
        validation_fixture_ids=validation_ids,
        candidate_metrics=TrainingMetrics(brier=0.18, log_loss=0.58),
        champion_metrics=TrainingMetrics(brier=0.19, log_loss=0.61),
        baseline_metrics=TrainingMetrics(brier=0.21, log_loss=0.65),
        approved=True,
        reason="candidate strictly improved both metrics",
    )
    fake_client.responses[("private", "rpc", "record_training_evaluation")] = [
        {
            "id": str(RUN_ID),
            "candidate_model_id": None,
            "decision": "failed",
            "parameter_hash": "a" * 64,
        }
    ]
    fake_client.responses[("private", "table", "model_versions")] = [model_row]

    recorded = gateway.record_training_evaluation(evaluation, RUN_ID)

    assert recorded.status == "failed"
    assert recorded.candidate_model_id is None
    assert recorded.active_model_id == MODEL_ID


def test_failed_reconciliation_reports_the_real_active_model(
    gateway, fake_client, model_row
):
    fake_client.responses[("private", "rpc", "finalize_training_evaluation")] = [
        {
            "id": str(RUN_ID),
            "candidate_model_id": str(CURRENT_MODEL_ID),
            "decision": "failed",
            "parameter_hash": "b" * 64,
        }
    ]
    fake_client.responses[("private", "table", "model_versions")] = [model_row]

    finalized = gateway.finalize_training_evaluation(RUN_ID)

    assert finalized.status == "failed"
    assert finalized.active_model_id == MODEL_ID
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
