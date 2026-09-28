from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Sequence
from uuid import UUID

from .domain import Fixture, JobClaim, LiveSnapshot, ModelVersion, PredictionRecord

if TYPE_CHECKING:
    from .training import CandidateEvaluation, TrainingExample, TrainingRun


class RepositoryUnavailable(RuntimeError):
    """Raised when the persistence transport cannot serve a request."""


class Repository(Protocol):
    def upsert_fixtures(self, fixtures: Sequence[Fixture]) -> int: ...

    def active_fixtures(self, limit: int = 12) -> list[Fixture]: ...

    def store_snapshot(self, snapshot: LiveSnapshot) -> UUID: ...

    def store_prediction(self, prediction: PredictionRecord) -> UUID: ...

    def active_model(self) -> ModelVersion: ...

    def training_examples(self, limit: int = 1000) -> list[TrainingExample]: ...

    def consumed_validation_ids(self, limit: int = 50000) -> set[UUID]: ...

    def recover_abandoned_training_evaluations(
        self, stale_after_seconds: int = 900, limit: int = 100
    ) -> int: ...

    def record_training_evaluation(
        self, evaluation: CandidateEvaluation
    ) -> TrainingRun: ...

    def promote_model(self, candidate_id: UUID, current_id: UUID) -> ModelVersion: ...

    def finalize_training_evaluation(self, run_id: UUID) -> TrainingRun: ...

    def public_live(self, limit: int, cursor: str | None) -> list[dict]: ...

    def public_match(self, public_id: UUID) -> dict | None: ...

    def public_model_status(self) -> dict: ...

    def claim_job(self, name: str, key: str, lease_seconds: int) -> JobClaim | None: ...

    def finish_job(
        self,
        run_id: UUID,
        request_id: UUID,
        state: str,
        counters: dict,
        error: str | None = None,
    ) -> None: ...
