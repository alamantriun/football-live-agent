import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from football_live.domain import (
    DataStatus,
    Fixture,
    HomeAwayFloat,
    JobClaim,
    ModelVersion,
    PredictionRecord,
)
from football_live.jobs import run_collect, run_discover, run_settle, run_train
from football_live.provider import (
    ProviderFixture,
    ProviderSnapshot,
    ProviderStats,
    ProviderUnavailable,
)
from football_live.training import TrainingRun


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
HTTP_REQUEST_ID = UUID("10000000-0000-0000-0000-000000000001")
RUN_ID = UUID("20000000-0000-0000-0000-000000000001")
FENCING_REQUEST_ID = UUID("30000000-0000-0000-0000-000000000001")
MODEL_ID = UUID("40000000-0000-0000-0000-000000000001")


def make_fixture(index: int, **changes) -> Fixture:
    fixture = Fixture(
        id=UUID(int=10_000 + index),
        public_id=UUID(int=20_000 + index),
        provider="365scores",
        provider_fixture_id=str(100_000 + index),
        competition="Liga de prueba",
        home_name=f"Local {index}",
        away_name=f"Visitante {index}",
        scheduled_at=NOW - timedelta(minutes=30),
        status="live",
        discovered_at=NOW - timedelta(hours=1),
        updated_at=NOW,
    )
    return fixture.model_copy(update=changes)


def make_provider_fixture(index: int) -> ProviderFixture:
    return ProviderFixture(
        provider_fixture_id=str(100_000 + index),
        competition="Liga de prueba",
        home_name=f"Local {index}",
        away_name=f"Visitante {index}",
        scheduled_at=NOW,
        status="live",
    )


def make_provider_snapshot(
    fixture: Fixture,
    *,
    status: str = "live",
    score_home: int = 1,
    score_away: int = 0,
    observed_at: datetime = NOW,
) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider_fixture_id=fixture.provider_fixture_id,
        home_name=fixture.home_name,
        away_name=fixture.away_name,
        minute=63,
        score_home=score_home,
        score_away=score_away,
        stats=ProviderStats(),
        provider_observed_at=observed_at,
        quality=DataStatus.DEGRADED,
        sanitized_provider_data={"status": status},
    )


def make_active_model() -> ModelVersion:
    return ModelVersion(
        id=MODEL_ID,
        version="champion",
        state="active",
        parameters={"factores": {"local": 1.0, "visitante": 1.0}},
        parameter_hash="champion-hash",
        code_version="previous",
        train_size=70,
        validation_size=30,
        brier=0.2,
        log_loss=0.6,
        created_at=NOW - timedelta(days=1),
        activated_at=NOW - timedelta(hours=12),
    )


class FakeRepository:
    def __init__(self) -> None:
        self.claim_job_result = JobClaim(
            run_id=RUN_ID,
            request_id=FENCING_REQUEST_ID,
        )
        self.claims = []
        self.finishes = []
        self.fixtures = []
        self.unfinished = []
        self.upserted = []
        self.snapshots = []
        self.predictions = []
        self.published_predictions = []
        self.outcomes = []
        self.reviews = []
        self.model = make_active_model()

    def claim_job(self, name, key, lease_seconds):
        self.claims.append((name, key, lease_seconds))
        return self.claim_job_result

    def finish_job(self, run_id, request_id, state, counters, error=None):
        self.finishes.append((run_id, request_id, state, dict(counters), error))

    def upsert_fixtures(self, fixtures):
        self.upserted.extend(fixtures)
        return len(fixtures)

    def active_fixtures(self, limit=12):
        return self.fixtures[:limit]

    def unfinished_fixtures(self, limit=30):
        return self.unfinished[:limit]

    def store_snapshot(self, snapshot):
        snapshot_id = UUID(int=30_000 + len(self.snapshots))
        self.snapshots.append(snapshot.model_copy(update={"id": snapshot_id}))
        return snapshot_id

    def store_prediction(self, prediction):
        self.predictions.append(prediction)
        return UUID(int=40_000 + len(self.predictions))

    def publish_live_prediction(self, fixture, snapshot, prediction, model):
        self.published_predictions.append((fixture, snapshot, prediction, model))

    def active_model(self):
        return self.model

    def store_confirmed_outcome(
        self, fixture, home_score, away_score, confirmed_at, source
    ):
        self.outcomes.append(
            (fixture.id, home_score, away_score, confirmed_at, source)
        )

    def flag_fixture_review(self, fixture, reason):
        self.reviews.append((fixture.id, reason))


class FakeProvider:
    def __init__(self, fixtures=None, snapshots=None, failures=None) -> None:
        self.fixtures = list(fixtures or [])
        self.snapshots = dict(snapshots or {})
        self.failures = set(failures or ())
        self.list_calls = 0
        self.snapshot_calls = []
        self.active = 0
        self.max_active = 0
        self.block = None

    async def list_live(self):
        self.list_calls += 1
        if self.block is not None:
            await self.block.wait()
        return self.fixtures

    async def get_snapshot(self, provider_fixture_id):
        self.snapshot_calls.append(provider_fixture_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            if provider_fixture_id in self.failures:
                raise ProviderUnavailable("raw provider payload must stay private")
            return self.snapshots[provider_fixture_id]
        finally:
            self.active -= 1


class FakePredictor:
    def __init__(self, stale_fixture_ids=()) -> None:
        self.stale_fixture_ids = set(stale_fixture_ids)
        self.calls = []

    def predict(self, snapshot, active_model):
        self.calls.append((snapshot, active_model))
        stale = snapshot.fixture_id in self.stale_fixture_ids
        return PredictionRecord(
            fixture_id=snapshot.fixture_id,
            snapshot_id=snapshot.id,
            model_version_id=active_model.id,
            lambda_base=HomeAwayFloat(home=0.0 if stale else 1.2, away=0.0 if stale else 0.8),
            lambda_adjusted=HomeAwayFloat(
                home=0.0 if stale else 1.2,
                away=0.0 if stale else 0.8,
            ),
            probabilities={} if stale else {"home": 50.0, "draw": 30.0, "away": 20.0},
            explanation={"persist": not stale, "retain_previous": stale},
            quality=DataStatus.STALE if stale else DataStatus.DEGRADED,
            created_at=NOW,
        )


class FakeTrainer:
    def __init__(self, result=None, error=None) -> None:
        self.result = result or TrainingRun(
            id=UUID(int=50_000),
            status="collecting",
            active_model_id=MODEL_ID,
        )
        self.error = error
        self.calls = []

    def run(self, repository, code_version):
        self.calls.append((repository, code_version))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize("job_name", ["discover", "collect", "settle", "train"])
async def test_duplicate_claim_does_zero_provider_or_service_work(job_name):
    repo = FakeRepository()
    repo.claim_job_result = None
    provider = FakeProvider()
    predictor = FakePredictor()
    trainer = FakeTrainer()

    if job_name == "discover":
        result = await run_discover(repo, provider, HTTP_REQUEST_ID, "discover:1")
    elif job_name == "collect":
        result = await run_collect(repo, provider, predictor, HTTP_REQUEST_ID, "collect:1")
    elif job_name == "settle":
        result = await run_settle(repo, provider, HTTP_REQUEST_ID, "settle:1")
    else:
        result = await run_train(repo, trainer, HTTP_REQUEST_ID, "train:1")

    assert result.status == "duplicate"
    assert provider.list_calls == 0
    assert provider.snapshot_calls == []
    assert predictor.calls == []
    assert trainer.calls == []
    assert repo.finishes == []


@pytest.mark.asyncio
async def test_discover_caps_provider_results_at_100_and_finishes_with_fencing_token():
    repo = FakeRepository()
    provider = FakeProvider([make_provider_fixture(index) for index in range(105)])

    result = await run_discover(
        repo,
        provider,
        HTTP_REQUEST_ID,
        "discover:2026-09-29T12:00Z",
    )

    assert result.status == "succeeded"
    assert len(repo.upserted) == 100
    assert repo.upserted[0].provider_fixture_id == "100000"
    assert repo.claims == [("discover", "discover:2026-09-29T12:00Z", 120)]
    assert repo.finishes == [
        (
            RUN_ID,
            FENCING_REQUEST_ID,
            "succeeded",
            {"provider_fixtures": 105, "stored": 100, "truncated": 5},
            None,
        )
    ]


@pytest.mark.asyncio
async def test_failed_job_is_sanitized_and_lease_is_finalized():
    class BrokenProvider(FakeProvider):
        async def list_live(self):
            raise ProviderUnavailable("secret upstream body")

    repo = FakeRepository()

    result = await run_discover(repo, BrokenProvider(), HTTP_REQUEST_ID, "discover:2")

    assert result.status == "failed"
    assert result.error == "provider_unavailable"
    assert "secret" not in repr(result)
    assert repo.finishes[-1][0:3] == (RUN_ID, FENCING_REQUEST_ID, "failed")
    assert repo.finishes[-1][-1] == "provider_unavailable"


@pytest.mark.asyncio
async def test_time_budget_cancels_work_and_still_finalizes_lease(monkeypatch):
    import football_live.jobs as jobs

    repo = FakeRepository()
    provider = FakeProvider()
    provider.block = asyncio.Event()
    monkeypatch.setattr(jobs, "JOB_TIME_BUDGET_SECONDS", 0.01)

    result = await run_discover(repo, provider, HTTP_REQUEST_ID, "discover:slow")

    assert result.status == "failed"
    assert result.error == "time_budget_exceeded"
    assert repo.finishes[-1][0:3] == (RUN_ID, FENCING_REQUEST_ID, "failed")


@pytest.mark.asyncio
async def test_collect_caps_at_12_uses_concurrency_two_and_skips_stale_sentinel():
    repo = FakeRepository()
    repo.fixtures = [make_fixture(index) for index in range(14)]
    snapshots = {
        fixture.provider_fixture_id: make_provider_snapshot(fixture)
        for fixture in repo.fixtures
    }
    failed_id = repo.fixtures[3].provider_fixture_id
    stale_fixture_id = repo.fixtures[7].id
    provider = FakeProvider(snapshots=snapshots, failures={failed_id})
    predictor = FakePredictor(stale_fixture_ids={stale_fixture_id})

    result = await run_collect(
        repo,
        provider,
        predictor,
        HTTP_REQUEST_ID,
        "collect:2026-09-29T12:00Z",
    )

    assert result.status == "succeeded"
    assert len(provider.snapshot_calls) == 12
    assert provider.max_active == 2
    assert len(repo.snapshots) == 11
    assert len(repo.predictions) == 10
    assert len(repo.published_predictions) == 10
    assert all(prediction.probabilities for prediction in repo.predictions)
    assert result.counters == {
        "selected": 12,
        "snapshots": 11,
        "predictions": 10,
        "published": 10,
        "skipped_predictions": 1,
        "provider_errors": 1,
        "processing_errors": 0,
    }
    assert repo.finishes[-1][2] == "succeeded"
    assert repo.finishes[-1][-1] is None


@pytest.mark.asyncio
async def test_collect_reports_the_processing_stage_without_exposing_error_details():
    repo = FakeRepository()
    fixture = make_fixture(1)
    repo.fixtures = [fixture]
    provider = FakeProvider(
        snapshots={fixture.provider_fixture_id: make_provider_snapshot(fixture)}
    )
    predictor = FakePredictor()

    def fail_snapshot_store(_snapshot):
        raise RuntimeError("private provider payload must not appear in counters")

    repo.store_snapshot = fail_snapshot_store

    result = await run_collect(
        repo,
        provider,
        predictor,
        HTTP_REQUEST_ID,
        "collect:processing-stage",
    )

    assert result.counters["processing_errors"] == 1
    assert result.counters["processing_error_stages"] == {"snapshot_store": 1}
    assert "private provider payload" not in str(result.counters)
    assert predictor.calls == []


@pytest.mark.asyncio
async def test_collect_reports_projection_store_failures_after_private_prediction_is_saved():
    repo = FakeRepository()
    fixture = make_fixture(1)
    repo.fixtures = [fixture]
    provider = FakeProvider(
        snapshots={fixture.provider_fixture_id: make_provider_snapshot(fixture)}
    )

    def fail_projection(*_args):
        raise RuntimeError("private projection failure details")

    repo.publish_live_prediction = fail_projection

    result = await run_collect(
        repo,
        provider,
        FakePredictor(),
        HTTP_REQUEST_ID,
        "collect:projection-stage",
    )

    assert len(repo.predictions) == 1
    assert result.counters["predictions"] == 1
    assert result.counters["published"] == 0
    assert result.counters["processing_error_stages"] == {"projection_store": 1}
    assert "private projection failure details" not in str(result.counters)


@pytest.mark.asyncio
async def test_settle_caps_at_30_confirms_finals_and_flags_corrections_for_review():
    repo = FakeRepository()
    repo.unfinished = [make_fixture(index) for index in range(35)]
    repo.unfinished[1] = repo.unfinished[1].model_copy(
        update={"final_home": 1, "final_away": 0}
    )
    snapshots = {
        fixture.provider_fixture_id: make_provider_snapshot(fixture)
        for fixture in repo.unfinished
    }
    snapshots[repo.unfinished[0].provider_fixture_id] = make_provider_snapshot(
        repo.unfinished[0], status="full time", score_home=2, score_away=1
    )
    snapshots[repo.unfinished[1].provider_fixture_id] = make_provider_snapshot(
        repo.unfinished[1], status="finished", score_home=2, score_away=0
    )
    provider = FakeProvider(snapshots=snapshots)

    result = await run_settle(
        repo,
        provider,
        HTTP_REQUEST_ID,
        "settle:2026-09-29T12:05Z",
    )

    assert result.status == "succeeded"
    assert len(provider.snapshot_calls) == 30
    assert repo.outcomes == [
        (repo.unfinished[0].id, 2, 1, NOW, "365scores")
    ]
    assert repo.reviews == [
        (repo.unfinished[1].id, "provider_score_correction")
    ]
    assert result.counters == {
        "selected": 30,
        "confirmed": 1,
        "review_required": 1,
        "not_final": 28,
        "provider_errors": 0,
        "processing_errors": 0,
    }


@pytest.mark.asyncio
async def test_training_failure_keeps_active_model_and_finishes_once():
    repo = FakeRepository()
    previous = repo.active_model()
    trainer = FakeTrainer(error=RuntimeError("secret training traceback"))

    result = await run_train(repo, trainer, HTTP_REQUEST_ID, "train:2026-09-29")

    assert result.status == "failed"
    assert result.error == "training_failed"
    assert repo.active_model().id == previous.id
    assert len(trainer.calls) == 1
    assert len(repo.finishes) == 1
    assert repo.finishes[0][0:3] == (RUN_ID, FENCING_REQUEST_ID, "failed")


@pytest.mark.asyncio
async def test_rejected_training_keeps_champion_and_records_rejected_terminal_state():
    repo = FakeRepository()
    previous = repo.active_model()
    trainer = FakeTrainer(
        result=TrainingRun(
            id=UUID(int=50_001),
            status="rejected",
            active_model_id=previous.id,
        )
    )

    result = await run_train(repo, trainer, HTTP_REQUEST_ID, "train:2026-09-30")

    assert result.status == "rejected"
    assert repo.active_model().id == previous.id
    assert len(trainer.calls) == 1
    assert repo.finishes[0][2:] == (
        "rejected",
        {"training_status": "rejected", "active_model_preserved": True},
        "candidate_rejected",
    )


@pytest.mark.asyncio
async def test_collecting_training_is_a_successful_noop_that_preserves_champion():
    repo = FakeRepository()
    previous = repo.active_model()

    result = await run_train(repo, FakeTrainer(), HTTP_REQUEST_ID, "train:collecting")

    assert result.status == "succeeded"
    assert result.counters == {
        "training_status": "collecting",
        "active_model_preserved": True,
    }
    assert repo.active_model().id == previous.id
    assert repo.finishes[-1][2:] == ("succeeded", result.counters, None)


@pytest.mark.asyncio
async def test_local_compatibility_cycle_delegates_to_the_four_idempotent_jobs():
    from recolector_aprendizaje import run_cycle

    repo = FakeRepository()
    provider = FakeProvider()
    predictor = FakePredictor()
    trainer = FakeTrainer()

    results = await run_cycle(
        repo,
        provider,
        predictor,
        trainer,
        request_id=HTTP_REQUEST_ID,
        at=NOW,
    )

    assert [result.status for result in results] == [
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    assert [claim[0] for claim in repo.claims] == [
        "discover",
        "collect",
        "settle",
        "train",
    ]
    assert repo.claims[0][1] == "discover:2026-09-29T12:00Z"
    assert repo.claims[1][1] == "collect:2026-09-29T12:00Z"
    assert repo.claims[2][1] == "settle:2026-09-29T12:00Z"
    assert repo.claims[3][1] == "train:2026-09-29"
