from typing import Protocol, Sequence
from uuid import UUID

from .domain import Fixture, JobClaim, LiveSnapshot, ModelVersion, PredictionRecord


class RepositoryUnavailable(RuntimeError):
    """Raised when the persistence transport cannot serve a request."""


class Repository(Protocol):
    def upsert_fixtures(self, fixtures: Sequence[Fixture]) -> int: ...

    def active_fixtures(self, limit: int = 12) -> list[Fixture]: ...

    def store_snapshot(self, snapshot: LiveSnapshot) -> UUID: ...

    def store_prediction(self, prediction: PredictionRecord) -> UUID: ...

    def active_model(self) -> ModelVersion: ...

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
