from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

import football_live.prediction_service as prediction_service_module
from calidad_vivo import classify_freshness
from football_live.domain import HomeAwayStat, LiveSnapshot, ModelVersion
from football_live.prediction_service import PredictionService


NOW = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
FIXTURE_ID = UUID("10000000-0000-0000-0000-000000000001")
SNAPSHOT_ID = UUID("20000000-0000-0000-0000-000000000002")
MODEL_ID = UUID("30000000-0000-0000-0000-000000000003")


@pytest.fixture
def service() -> PredictionService:
    return PredictionService(clock=lambda: NOW)


@pytest.fixture
def snapshot() -> LiveSnapshot:
    return LiveSnapshot(
        id=SNAPSHOT_ID,
        fixture_id=FIXTURE_ID,
        minute=63,
        score_home=1,
        score_away=0,
        stats={
            "possession": HomeAwayStat(home=55, away=45),
            "shots": HomeAwayStat(home=8, away=3),
            "shots_on_target": HomeAwayStat(home=4, away=2),
            "corners": HomeAwayStat(home=5, away=1),
            "yellow_cards": HomeAwayStat(home=1, away=2),
            "red_cards": HomeAwayStat(home=0, away=0),
            "fouls": HomeAwayStat(home=7, away=9),
            "offsides": HomeAwayStat(home=2, away=1),
            "expected_goals": HomeAwayStat(home=1.37, away=0),
        },
        sanitized_provider_data={"status": "2º Tiempo"},
        provider_observed_at=NOW - timedelta(seconds=30),
        collected_at=NOW - timedelta(seconds=20),
        quality="fresh",
    )


@pytest.fixture
def active_model() -> ModelVersion:
    return ModelVersion(
        id=MODEL_ID,
        version="live-fit-test",
        state="active",
        parameters={
            "factores": {"local": 1.1, "visitante": 0.9},
            "prior_local": 1.6,
            "prior_visitante": 1.1,
        },
        parameter_hash="sha256:test",
        code_version="465b13e",
        train_size=70,
        validation_size=30,
        brier=0.19,
        log_loss=0.61,
        created_at=NOW - timedelta(days=1),
        activated_at=NOW - timedelta(hours=12),
    )


@pytest.mark.parametrize(
    ("age_seconds", "completeness", "expected"),
    [
        (60, "fresh", "fresh"),
        (60, "degraded", "degraded"),
        (61, "fresh", "stale"),
        (120, "degraded", "stale"),
        (121, "fresh", "suspended"),
        (0, "suspended", "suspended"),
    ],
)
def test_freshness_boundaries(age_seconds, completeness, expected):
    observed_at = NOW - timedelta(seconds=age_seconds)

    assert classify_freshness(observed_at, NOW, completeness) == expected


def test_stale_snapshot_is_a_retain_previous_sentinel(
    service, snapshot, active_model, monkeypatch
):
    snapshot.provider_observed_at = NOW - timedelta(seconds=90)

    def fail_if_recomputed(*_args, **_kwargs):
        raise AssertionError("stale snapshots must not run the simulator")

    monkeypatch.setattr(prediction_service_module, "correr", fail_if_recomputed)

    result = service.predict(snapshot, active_model)

    assert result.quality == "stale"
    assert result.probabilities == {}
    assert result.lambda_base.model_dump() == {"home": 0.0, "away": 0.0}
    assert result.lambda_adjusted.model_dump() == {"home": 0.0, "away": 0.0}
    assert result.explanation["retain_previous"] is True
    assert result.explanation["persist"] is False
    assert "previous" in result.explanation["reason"].lower()


def test_snapshot_over_120_seconds_is_suspended_without_recomputation(
    service, snapshot, active_model, monkeypatch
):
    snapshot.provider_observed_at = NOW - timedelta(seconds=121)
    monkeypatch.setattr(
        prediction_service_module,
        "correr",
        lambda *_args, **_kwargs: pytest.fail("suspended snapshots must not run"),
    )

    result = service.predict(snapshot, active_model)

    assert result.quality == "suspended"
    assert result.probabilities == {}
    assert result.explanation["retain_previous"] is False
    assert result.explanation["persist"] is False


def test_snapshot_quality_suspended_emits_no_derived_probabilities(
    service, snapshot, active_model
):
    suspended = snapshot.model_copy(update={"quality": "suspended"})

    result = service.predict(suspended, active_model)

    assert result.quality == "suspended"
    assert result.probabilities == {}


def test_1x2_probabilities_share_one_distribution(service, snapshot, active_model):
    result = service.predict(snapshot, active_model)

    total = sum(result.probabilities[key] for key in ("home", "draw", "away"))
    assert total == pytest.approx(100.0)
    assert result.snapshot_id == SNAPSHOT_ID
    assert result.fixture_id == FIXTURE_ID
    assert result.model_version_id == MODEL_ID
    assert result.quality == "fresh"


def test_explanation_preserves_model_lambdas_inputs_and_missing_values(
    service, snapshot, active_model
):
    partial = snapshot.model_copy(deep=True)
    partial.stats["shots_on_target"].away = None
    partial.quality = "degraded"

    result = service.predict(partial, active_model)
    explanation = result.explanation

    assert result.quality == "degraded"
    assert explanation["model_version"] == "live-fit-test"
    assert explanation["lambda_base"] == result.lambda_base.model_dump()
    assert explanation["lambda_adjusted"] == result.lambda_adjusted.model_dump()
    assert result.lambda_adjusted.home == pytest.approx(result.lambda_base.home * 1.1)
    assert result.lambda_adjusted.away == pytest.approx(result.lambda_base.away * 0.9)
    assert explanation["inputs_used"]["stats"]["shots_on_target"] == {
        "home": 4,
        "away": None,
    }
    assert explanation["inputs_used"]["stats"]["expected_goals"]["away"] == 0
    assert explanation["warnings"]


def test_snapshot_must_be_persisted(service, snapshot, active_model):
    transient = snapshot.model_copy(update={"id": None})

    with pytest.raises(ValueError, match="persisted snapshot"):
        service.predict(transient, active_model)


@pytest.mark.parametrize("state", ["candidate", "rejected", "retired"])
def test_only_active_model_is_accepted(service, snapshot, active_model, state):
    inactive = active_model.model_copy(update={"state": state})

    with pytest.raises(ValueError, match="active model"):
        service.predict(snapshot, inactive)


@pytest.mark.parametrize("status", ["finished", "finalizado", "disputed"])
def test_finished_or_disputed_snapshot_is_suspended(
    service, snapshot, active_model, status
):
    closed = snapshot.model_copy(
        update={"sanitized_provider_data": {"status": status}}
    )

    result = service.predict(closed, active_model)

    assert result.quality == "suspended"
    assert result.probabilities == {}


def test_missing_required_minute_is_suspended(service, snapshot, active_model):
    impossible = snapshot.model_copy(update={"minute": None})

    result = service.predict(impossible, active_model)

    assert result.quality == "suspended"
    assert result.probabilities == {}
