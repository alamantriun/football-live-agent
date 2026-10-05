from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from math import isclose
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)


Probability = Annotated[FiniteFloat, Field(ge=0.0, le=100.0)]
NonNegativeFloat = Annotated[FiniteFloat, Field(ge=0.0)]
Score = Annotated[int, Field(ge=0, le=30)]
MatchMinute = Annotated[int, Field(ge=0, le=150)]
StatValue = int | FiniteFloat | None


class StrictDomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataStatus(StrEnum):
    FRESH = "fresh"
    DEGRADED = "degraded"
    STALE = "stale"
    SUSPENDED = "suspended"


class JobClaim(StrictDomainModel):
    run_id: UUID
    request_id: UUID


class HomeAwayStat(StrictDomainModel):
    home: StatValue
    away: StatValue

    def __getitem__(self, side: Literal["home", "away"]) -> StatValue:
        return getattr(self, side)


class HomeAwayFloat(StrictDomainModel):
    home: NonNegativeFloat
    away: NonNegativeFloat


class MatchEvent(StrictDomainModel):
    provider_order: int = Field(ge=1, le=10_000)
    minute: MatchMinute
    added_time: int | None = Field(default=None, ge=0, le=30)
    side: Literal["home", "away"]
    kind: Literal["goal", "yellow_card", "red_card"]


class Fixture(StrictDomainModel):
    id: UUID | None = None
    public_id: UUID | None = None
    provider: Literal["365scores"]
    provider_fixture_id: Annotated[str, Field(min_length=1)]
    competition: Annotated[str, Field(min_length=1)]
    home_name: Annotated[str, Field(min_length=1)]
    away_name: Annotated[str, Field(min_length=1)]
    home_logo_url: str | None = None
    away_logo_url: str | None = None
    scheduled_at: AwareDatetime | None = None
    status: Annotated[str, Field(min_length=1)]
    final_home: Score | None = None
    final_away: Score | None = None
    discovered_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None


class LiveSnapshot(StrictDomainModel):
    id: UUID | None = None
    fixture_id: UUID
    minute: MatchMinute | None
    score_home: Score
    score_away: Score
    stats: dict[str, HomeAwayStat]
    sanitized_provider_data: dict[str, Any] = Field(default_factory=dict)
    provider_observed_at: AwareDatetime
    collected_at: AwareDatetime | None = None
    quality: DataStatus
    observation_bucket: AwareDatetime | None = None

    @model_validator(mode="after")
    def derive_observation_bucket(self) -> LiveSnapshot:
        bucket = self.provider_observed_at.replace(second=0, microsecond=0)
        object.__setattr__(self, "observation_bucket", bucket)
        return self


class PredictionRecord(StrictDomainModel):
    id: UUID | None = None
    fixture_id: UUID
    snapshot_id: UUID
    model_version_id: UUID
    lambda_base: HomeAwayFloat
    lambda_adjusted: HomeAwayFloat
    probabilities: dict[str, Probability]
    explanation: dict[str, Any]
    quality: DataStatus
    created_at: AwareDatetime | None = None

    @field_validator("probabilities")
    @classmethod
    def require_canonical_side_names(
        cls, probabilities: dict[str, Probability]
    ) -> dict[str, Probability]:
        required = {"home", "draw", "away"}
        if probabilities and not required.issubset(probabilities):
            raise ValueError(
                "non-empty probabilities must include canonical home/draw/away keys"
            )
        unsupported = {"local", "visitante", "casa", "fuera"}.intersection(
            probabilities
        )
        if unsupported:
            raise ValueError("probability keys must use canonical home/away names")
        if probabilities:
            canonical_total = sum(probabilities[key] for key in required)
            if not isclose(canonical_total, 100.0, rel_tol=0.0, abs_tol=1e-6):
                raise ValueError("canonical home/draw/away probabilities must total 100")
        return probabilities


class ModelVersion(StrictDomainModel):
    id: UUID
    version: Annotated[str, Field(min_length=1)]
    state: Literal["candidate", "active", "rejected", "retired"]
    parameters: dict[str, Any]
    parameter_hash: Annotated[str, Field(min_length=1)]
    code_version: Annotated[str, Field(min_length=1)]
    previous_model_id: UUID | None = None
    train_size: Annotated[int, Field(ge=0)] = 0
    validation_size: Annotated[int, Field(ge=0)] = 0
    brier: NonNegativeFloat | None = None
    log_loss: NonNegativeFloat | None = None
    created_at: AwareDatetime
    activated_at: AwareDatetime | None = None
