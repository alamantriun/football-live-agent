from __future__ import annotations

import asyncio
import os
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from .domain import Fixture, LiveSnapshot, StrictDomainModel
from .provider import ProviderFixture, ProviderSnapshot, ProviderUnavailable
from .repository import RepositoryUnavailable


JOB_TIME_BUDGET_SECONDS = 55.0
DISCOVER_LEASE_SECONDS = 120
COLLECT_LEASE_SECONDS = 120
SETTLE_LEASE_SECONDS = 180
TRAIN_LEASE_SECONDS = 900

_FINAL_STATUSES = frozenset(
    {"ft", "finished", "ended", "finalizado", "full time", "final"}
)
_DISPUTED_MARKERS = ("disputed", "disputa", "correction", "correccion")


class JobResult(StrictDomainModel):
    status: Literal["duplicate", "succeeded", "failed", "rejected"]
    request_id: UUID
    run_id: UUID | None = None
    counters: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


def _plain_status(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(
        "".join(char for char in normalized if not unicodedata.combining(char))
        .lower()
        .split()
    )


def _error_code(exc: BaseException, fallback: str) -> str:
    if isinstance(exc, ProviderUnavailable):
        return "provider_unavailable"
    if isinstance(exc, RepositoryUnavailable):
        return "repository_unavailable"
    if isinstance(exc, TimeoutError):
        return "time_budget_exceeded"
    return fallback


def _fixture_from_provider(item: ProviderFixture) -> Fixture:
    return Fixture(
        provider="365scores",
        provider_fixture_id=item.provider_fixture_id,
        competition=item.competition,
        home_name=item.home_name,
        away_name=item.away_name,
        home_logo_url=item.home_logo_url,
        away_logo_url=item.away_logo_url,
        scheduled_at=item.scheduled_at,
        status=item.status,
    )


def _snapshot_from_provider(fixture: Fixture, item: ProviderSnapshot) -> LiveSnapshot:
    if fixture.id is None:
        raise ValueError("persisted fixture is required")
    if item.provider_fixture_id != fixture.provider_fixture_id:
        raise ValueError("provider fixture mismatch")
    return LiveSnapshot(
        fixture_id=fixture.id,
        minute=item.minute,
        score_home=item.score_home,
        score_away=item.score_away,
        stats=item.stats.model_dump(),
        sanitized_provider_data=item.sanitized_provider_data,
        provider_observed_at=item.provider_observed_at,
        quality=item.quality,
    )


def _fixture_with_provider_logos(
    fixture: Fixture, item: ProviderSnapshot
) -> Fixture:
    updates = {}
    if item.home_logo_url and item.home_logo_url != fixture.home_logo_url:
        updates["home_logo_url"] = item.home_logo_url
    if item.away_logo_url and item.away_logo_url != fixture.away_logo_url:
        updates["away_logo_url"] = item.away_logo_url
    return fixture.model_copy(update=updates) if updates else fixture


async def _run_claimed(
    *,
    repo: Any,
    name: str,
    key: str,
    request_id: UUID,
    lease_seconds: int,
    failure_code: str,
    work: Callable[[], Awaitable[tuple[str, dict[str, Any], str | None]]],
) -> JobResult:
    claim = repo.claim_job(name, key, lease_seconds)
    if claim is None:
        return JobResult(status="duplicate", request_id=request_id)

    terminal_status = "failed"
    counters: dict[str, Any] = {}
    error: str | None = failure_code
    try:
        async with asyncio.timeout(JOB_TIME_BUDGET_SECONDS):
            terminal_status, counters, error = await work()
    except Exception as exc:
        error = _error_code(exc, failure_code)
        terminal_status = "failed"
    finally:
        repo.finish_job(
            claim.run_id,
            claim.request_id,
            terminal_status,
            counters,
            error,
        )

    return JobResult(
        status=terminal_status,
        request_id=request_id,
        run_id=claim.run_id,
        counters=counters,
        error=error,
    )


async def run_discover(repo, provider, request_id: UUID, key: str) -> JobResult:
    async def work() -> tuple[str, dict[str, Any], None]:
        provider_fixtures = await provider.list_live()
        selected = provider_fixtures[:100]
        stored = repo.upsert_fixtures(
            [_fixture_from_provider(item) for item in selected]
        )
        return (
            "succeeded",
            {
                "provider_fixtures": len(provider_fixtures),
                "stored": stored,
                "truncated": max(0, len(provider_fixtures) - len(selected)),
            },
            None,
        )

    return await _run_claimed(
        repo=repo,
        name="discover",
        key=key,
        request_id=request_id,
        lease_seconds=DISCOVER_LEASE_SECONDS,
        failure_code="discovery_failed",
        work=work,
    )


async def run_collect(repo, provider, predictor, request_id: UUID, key: str) -> JobResult:
    async def work() -> tuple[str, dict[str, Any], None]:
        fixtures = repo.active_fixtures(limit=12)[:12]
        counters = {
            "selected": len(fixtures),
            "snapshots": 0,
            "predictions": 0,
            "published": 0,
            "skipped_predictions": 0,
            "provider_errors": 0,
            "processing_errors": 0,
        }
        if not fixtures:
            return "succeeded", counters, None
        active_model = repo.active_model()
        semaphore = asyncio.Semaphore(2)

        async def collect_one(fixture: Fixture) -> None:
            async with semaphore:
                try:
                    provider_snapshot = await provider.get_snapshot(
                        fixture.provider_fixture_id
                    )
                except Exception:
                    counters["provider_errors"] += 1
                    return

                hydrated_fixture = _fixture_with_provider_logos(
                    fixture, provider_snapshot
                )
                if hydrated_fixture is not fixture:
                    try:
                        repo.upsert_fixtures([hydrated_fixture])
                        repo.sync_live_fixture_logos(hydrated_fixture)
                        fixture = hydrated_fixture
                    except Exception:
                        counters["processing_errors"] += 1
                        stage_counts = counters.setdefault(
                            "processing_error_stages", {}
                        )
                        stage_counts["fixture_logos"] = (
                            stage_counts.get("fixture_logos", 0) + 1
                        )
                        return

                status = _plain_status(
                    provider_snapshot.sanitized_provider_data.get("status")
                )
                if status in _FINAL_STATUSES or any(
                    marker in status for marker in _DISPUTED_MARKERS
                ):
                    counters["skipped_predictions"] += 1
                    return

                stage = "snapshot_build"
                try:
                    snapshot = _snapshot_from_provider(fixture, provider_snapshot)
                    stage = "snapshot_store"
                    snapshot_id = repo.store_snapshot(snapshot)
                    counters["snapshots"] += 1
                    persisted_snapshot = snapshot.model_copy(
                        update={"id": snapshot_id}
                    )
                    stage = "prediction"
                    prediction = predictor.predict(persisted_snapshot, active_model)
                    if (
                        not prediction.probabilities
                        or prediction.explanation.get("persist") is False
                    ):
                        counters["skipped_predictions"] += 1
                        return
                    stage = "prediction_store"
                    repo.store_prediction(prediction)
                    counters["predictions"] += 1
                    stage = "projection_store"
                    repo.publish_live_prediction(
                        fixture,
                        persisted_snapshot,
                        prediction,
                        active_model,
                    )
                    counters["published"] += 1
                except Exception:
                    counters["processing_errors"] += 1
                    stage_counts = counters.setdefault("processing_error_stages", {})
                    stage_counts[stage] = stage_counts.get(stage, 0) + 1

        await asyncio.gather(*(collect_one(fixture) for fixture in fixtures))
        return "succeeded", counters, None

    return await _run_claimed(
        repo=repo,
        name="collect",
        key=key,
        request_id=request_id,
        lease_seconds=COLLECT_LEASE_SECONDS,
        failure_code="collection_failed",
        work=work,
    )


async def run_settle(repo, provider, request_id: UUID, key: str) -> JobResult:
    async def work() -> tuple[str, dict[str, Any], None]:
        fixtures = repo.unfinished_fixtures(limit=30)[:30]
        counters = {
            "selected": len(fixtures),
            "confirmed": 0,
            "review_required": 0,
            "not_final": 0,
            "provider_errors": 0,
            "processing_errors": 0,
        }
        semaphore = asyncio.Semaphore(6)

        async def settle_one(fixture: Fixture) -> None:
            async with semaphore:
                try:
                    provider_snapshot = await provider.get_snapshot(
                        fixture.provider_fixture_id
                    )
                except Exception:
                    counters["provider_errors"] += 1
                    return
                try:
                    if provider_snapshot.provider_fixture_id != fixture.provider_fixture_id:
                        raise ValueError("provider fixture mismatch")
                    status = _plain_status(
                        provider_snapshot.sanitized_provider_data.get("status")
                    )
                    if any(marker in status for marker in _DISPUTED_MARKERS):
                        repo.flag_fixture_review(
                            fixture, "provider_score_correction"
                        )
                        counters["review_required"] += 1
                        return
                    if status not in _FINAL_STATUSES:
                        counters["not_final"] += 1
                        return
                    existing = (fixture.final_home, fixture.final_away)
                    observed = (
                        provider_snapshot.score_home,
                        provider_snapshot.score_away,
                    )
                    if all(value is not None for value in existing) and existing != observed:
                        repo.flag_fixture_review(
                            fixture, "provider_score_correction"
                        )
                        counters["review_required"] += 1
                        return
                    repo.store_confirmed_outcome(
                        fixture,
                        provider_snapshot.score_home,
                        provider_snapshot.score_away,
                        provider_snapshot.provider_observed_at,
                        "365scores",
                    )
                    counters["confirmed"] += 1
                except Exception:
                    counters["processing_errors"] += 1

        await asyncio.gather(*(settle_one(fixture) for fixture in fixtures))
        return "succeeded", counters, None

    return await _run_claimed(
        repo=repo,
        name="settle",
        key=key,
        request_id=request_id,
        lease_seconds=SETTLE_LEASE_SECONDS,
        failure_code="settlement_failed",
        work=work,
    )


def _runtime_code_version() -> str:
    return (
        os.environ.get("VERCEL_GIT_COMMIT_SHA")
        or os.environ.get("GIT_COMMIT")
        or "local"
    )


async def run_train(repo, trainer, request_id: UUID, key: str) -> JobResult:
    async def work() -> tuple[str, dict[str, Any], str | None]:
        previous = repo.active_model()
        training = trainer.run(repo, _runtime_code_version())
        active_after = repo.active_model()
        preserved = (
            training.status == "promoted" or active_after.id == previous.id
        )
        counters = {
            "training_status": training.status,
            "active_model_preserved": preserved,
        }
        if training.status == "rejected":
            return "rejected", counters, "candidate_rejected"
        if training.status == "failed":
            return "failed", counters, "training_failed"
        if not preserved:
            return "failed", counters, "active_model_changed_unexpectedly"
        return "succeeded", counters, None

    return await _run_claimed(
        repo=repo,
        name="train",
        key=key,
        request_id=request_id,
        lease_seconds=TRAIN_LEASE_SECONDS,
        failure_code="training_failed",
        work=work,
    )
