from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import hashlib
import json
import math
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field, FiniteFloat, model_validator

from evaluar_modelo import puntuaciones, vector
from modelo_poisson import predecir

from .domain import HomeAwayFloat, ModelVersion, Score, StrictDomainModel


MIN_TRAIN = 70
MIN_VALID = 30
BASELINE_FACTORS = {"local": 1.0, "visitante": 1.0}


class TrainingObservation(StrictDomainModel):
    observed_at: AwareDatetime
    minute: Annotated[int, Field(ge=15, le=80)]
    score_home: Score
    score_away: Score
    lambda_base: HomeAwayFloat


class TrainingExample(StrictDomainModel):
    fixture_id: UUID
    first_observed_at: AwareDatetime
    outcome_confirmed_at: AwareDatetime
    final_home: Score
    final_away: Score
    observations: tuple[TrainingObservation, ...]

    @model_validator(mode="after")
    def validate_evidence_order(self) -> TrainingExample:
        if not self.observations:
            raise ValueError("at least one training observation is required")
        if self.first_observed_at != min(item.observed_at for item in self.observations):
            raise ValueError("first_observed_at must match the earliest observation")
        for item in self.observations:
            if item.observed_at >= self.outcome_confirmed_at:
                raise ValueError("observations must precede outcome confirmation")
            if item.score_home > self.final_home or item.score_away > self.final_away:
                raise ValueError("observed scores cannot exceed the confirmed outcome")
        return self


class TrainingMetrics(StrictDomainModel):
    brier: Annotated[FiniteFloat, Field(ge=0.0)]
    log_loss: Annotated[FiniteFloat, Field(ge=0.0)]


class TrainingDecision(StrictDomainModel):
    parameters: dict[str, Any]
    candidate_metrics: TrainingMetrics
    champion_metrics: TrainingMetrics
    baseline_metrics: TrainingMetrics
    approved: bool
    reason: str


class CandidateEvaluation(TrainingDecision):
    version: Annotated[str, Field(min_length=1)]
    parameter_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    code_version: Annotated[str, Field(min_length=1)]
    previous_model_id: UUID
    train_fixture_ids: tuple[UUID, ...]
    validation_fixture_ids: tuple[UUID, ...]

    @model_validator(mode="after")
    def validate_sample_sizes(self) -> CandidateEvaluation:
        if len(self.train_fixture_ids) < MIN_TRAIN:
            raise ValueError("at least 70 training fixtures are required")
        if len(self.validation_fixture_ids) != MIN_VALID:
            raise ValueError("exactly 30 validation fixtures are required")
        if len(set(self.train_fixture_ids)) != len(self.train_fixture_ids):
            raise ValueError("training fixture IDs must be unique")
        if len(set(self.validation_fixture_ids)) != len(self.validation_fixture_ids):
            raise ValueError("validation fixture IDs must be unique")
        if set(self.train_fixture_ids).intersection(self.validation_fixture_ids):
            raise ValueError("training and validation fixture IDs cannot overlap")
        return self


class TrainingRun(StrictDomainModel):
    id: UUID | None = None
    candidate_model_id: UUID | None = None
    status: Literal["collecting", "candidate", "rejected", "promoted"]
    train_fixture_ids: tuple[UUID, ...] = ()
    validation_fixture_ids: tuple[UUID, ...] = ()
    parameter_hash: str | None = None
    active_model_id: UUID


class TrainingRepository(Protocol):
    def training_examples(self, limit: int = 1000) -> list[TrainingExample]: ...

    def consumed_validation_ids(self, limit: int = 50000) -> set[UUID]: ...

    def active_model(self) -> ModelVersion: ...

    def record_training_evaluation(
        self, evaluation: CandidateEvaluation
    ) -> TrainingRun: ...

    def promote_model(self, candidate_id: UUID, current_id: UUID) -> ModelVersion: ...


def build_chronological_split(
    examples: Sequence[TrainingExample],
    consumed_ids: set[UUID],
    min_train: int = MIN_TRAIN,
    min_valid: int = MIN_VALID,
) -> tuple[list[TrainingExample], list[TrainingExample]]:
    ordered = sorted(examples, key=lambda item: (item.first_observed_at, item.fixture_id))
    unconsumed = [item for item in ordered if item.fixture_id not in consumed_ids]
    if len(unconsumed) < min_valid:
        return [], []
    validation = unconsumed[-min_valid:]
    validation_ids = {item.fixture_id for item in validation}
    cutoff = validation[0].first_observed_at
    train = [
        item
        for item in ordered
        if item.fixture_id not in validation_ids and item.outcome_confirmed_at < cutoff
    ]
    if len(train) < min_train:
        return [], []
    return train, validation


def _factors(parameters: dict[str, Any]) -> dict[str, float]:
    raw = parameters.get("factores", parameters.get("factors", {}))
    if not isinstance(raw, dict):
        return BASELINE_FACTORS.copy()
    try:
        factors = {
            "local": float(raw.get("local", raw.get("home", 1.0))),
            "visitante": float(raw.get("visitante", raw.get("away", 1.0))),
        }
    except (TypeError, ValueError):
        return BASELINE_FACTORS.copy()
    if not all(math.isfinite(value) and 0.6 <= value <= 1.6 for value in factors.values()):
        return BASELINE_FACTORS.copy()
    return factors


def fit_factors(examples: Sequence[TrainingExample]) -> dict[str, float]:
    numerators = [20.0, 20.0]
    denominators = [20.0, 20.0]
    for example in examples:
        weight = 1.0 / len(example.observations)
        for observation in example.observations:
            remaining = (
                example.final_home - observation.score_home,
                example.final_away - observation.score_away,
            )
            bases = (observation.lambda_base.home, observation.lambda_base.away)
            for index in range(2):
                numerators[index] += weight * remaining[index]
                denominators[index] += weight * bases[index]
    return {
        side: max(0.6, min(1.6, numerators[index] / denominators[index]))
        for index, side in enumerate(("local", "visitante"))
    }


def score_examples(
    examples: Sequence[TrainingExample], factors: dict[str, float]
) -> TrainingMetrics:
    if not examples:
        raise ValueError("validation examples are required")
    per_fixture = []
    for example in examples:
        scores = []
        outcome = (
            0
            if example.final_home > example.final_away
            else 1
            if example.final_home == example.final_away
            else 2
        )
        for observation in example.observations:
            prediction = predecir(
                observation.lambda_base.home * factors["local"],
                observation.lambda_base.away * factors["visitante"],
                {
                    "local": observation.score_home,
                    "visitante": observation.score_away,
                },
                muestras=0,
            )
            values = puntuaciones(vector(prediction), outcome)
            scores.append(values)
        per_fixture.append(
            {
                metric: sum(row[metric] for row in scores) / len(scores)
                for metric in ("brier", "log_loss")
            }
        )
    metrics = {
        metric: sum(row[metric] for row in per_fixture) / len(per_fixture)
        for metric in ("brier", "log_loss")
    }
    return TrainingMetrics.model_validate(metrics)


def promotion_allowed(candidate: Any, champion: Any, baseline: Any) -> bool:
    def values(metrics: Any) -> tuple[float, float]:
        if isinstance(metrics, TrainingMetrics):
            return metrics.brier, metrics.log_loss
        return float(metrics["brier"]), float(metrics["log_loss"])

    try:
        candidate_brier, candidate_log_loss = values(candidate)
        champion_brier, champion_log_loss = values(champion)
        baseline_brier, baseline_log_loss = values(baseline)
    except (KeyError, TypeError, ValueError):
        return False
    if not all(
        math.isfinite(value)
        for value in (
            candidate_brier,
            candidate_log_loss,
            champion_brier,
            champion_log_loss,
            baseline_brier,
            baseline_log_loss,
        )
    ):
        return False
    return (
        candidate_brier < champion_brier
        and candidate_log_loss < champion_log_loss
        and candidate_brier < baseline_brier
        and candidate_log_loss < baseline_log_loss
    )


def evaluate_candidate(
    train: Sequence[TrainingExample],
    validation: Sequence[TrainingExample],
    active_model: ModelVersion,
) -> TrainingDecision:
    factors = fit_factors(train)
    parameters = dict(active_model.parameters)
    parameters["factores"] = factors
    candidate = score_examples(validation, factors)
    champion = score_examples(validation, _factors(active_model.parameters))
    baseline = score_examples(validation, BASELINE_FACTORS)
    approved = promotion_allowed(candidate, champion, baseline)
    reason = (
        "candidate strictly improved both metrics"
        if approved
        else "candidate did not strictly improve both metrics"
    )
    return TrainingDecision(
        parameters=parameters,
        candidate_metrics=candidate,
        champion_metrics=champion,
        baseline_metrics=baseline,
        approved=approved,
        reason=reason,
    )


def canonical_candidate_hash(
    parameters: dict[str, Any],
    train_fixture_ids: Sequence[UUID],
    validation_fixture_ids: Sequence[UUID],
    code_version: str,
) -> str:
    payload = {
        "parameters": parameters,
        "train_fixture_ids": [str(item) for item in train_fixture_ids],
        "validation_fixture_ids": [str(item) for item in validation_fixture_ids],
        "code_version": code_version,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TrainingService:
    def run(self, repository: TrainingRepository, code_version: str) -> TrainingRun:
        if not code_version.strip():
            raise ValueError("code_version is required")
        active = repository.active_model()
        examples = repository.training_examples()
        consumed = repository.consumed_validation_ids()
        train, validation = build_chronological_split(examples, consumed)
        if not train or not validation:
            return TrainingRun(status="collecting", active_model_id=active.id)

        decision = evaluate_candidate(train, validation, active)
        train_ids = tuple(item.fixture_id for item in train)
        validation_ids = tuple(item.fixture_id for item in validation)
        parameter_hash = canonical_candidate_hash(
            decision.parameters,
            train_ids,
            validation_ids,
            code_version,
        )
        evaluation = CandidateEvaluation(
            **decision.model_dump(),
            version=f"live-fit-{parameter_hash[:12]}",
            parameter_hash=parameter_hash,
            code_version=code_version,
            previous_model_id=active.id,
            train_fixture_ids=train_ids,
            validation_fixture_ids=validation_ids,
        )
        recorded = repository.record_training_evaluation(evaluation)
        if not decision.approved:
            return recorded
        if recorded.candidate_model_id is None:
            raise RuntimeError("persisted candidate ID is required for promotion")
        promoted = repository.promote_model(recorded.candidate_model_id, active.id)
        return recorded.model_copy(
            update={"status": "promoted", "active_model_id": promoted.id}
        )
