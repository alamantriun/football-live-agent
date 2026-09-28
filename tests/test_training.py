from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

import aprendizaje

from football_live.domain import ModelVersion
from football_live.training import (
    CandidateEvaluation,
    TrainingExample,
    TrainingObservation,
    TrainingRun,
    TrainingService,
    build_chronological_split,
    canonical_candidate_hash,
    evaluate_candidate,
    promotion_allowed,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
ACTIVE_ID = UUID("10000000-0000-0000-0000-000000000001")
RUN_ID = UUID("20000000-0000-0000-0000-000000000001")
CANDIDATE_ID = UUID("30000000-0000-0000-0000-000000000001")


def make_examples(count: int = 100) -> list[TrainingExample]:
    examples = []
    for index in range(count):
        observed_at = START + timedelta(days=index)
        examples.append(
            TrainingExample(
                fixture_id=UUID(int=index + 1),
                first_observed_at=observed_at,
                outcome_confirmed_at=observed_at + timedelta(hours=2),
                final_home=2,
                final_away=0,
                observations=(
                    TrainingObservation(
                        observed_at=observed_at,
                        minute=60,
                        score_home=0,
                        score_away=0,
                        lambda_base={"home": 0.8, "away": 0.2},
                    ),
                ),
            )
        )
    return examples


def make_active(factors: dict[str, float]) -> ModelVersion:
    return ModelVersion(
        id=ACTIVE_ID,
        version="champion",
        state="active",
        parameters={"factores": factors},
        parameter_hash="champion-hash",
        code_version="previous-code",
        train_size=70,
        validation_size=30,
        brier=0.60,
        log_loss=1.00,
        created_at=START,
        activated_at=START,
    )


def test_split_uses_only_outcomes_known_before_exact_validation_holdout():
    examples = make_examples()

    train, validation = build_chronological_split(examples, set())

    cutoff = min(item.first_observed_at for item in validation)
    assert len(train) == 70
    assert len(validation) == 30
    assert all(item.outcome_confirmed_at < cutoff for item in train)
    assert max(item.first_observed_at for item in train) < cutoff


def test_split_never_reuses_consumed_validation_ids():
    examples = make_examples(101)
    consumed = {examples[-1].fixture_id}

    train, validation = build_chronological_split(examples, consumed)

    assert len(validation) == 30
    assert consumed.isdisjoint(item.fixture_id for item in validation)
    assert validation[-1].fixture_id == examples[-2].fixture_id


def test_split_returns_no_holdout_until_both_sample_gates_are_met():
    train, validation = build_chronological_split(make_examples(99), set())

    assert train == []
    assert validation == []


@pytest.mark.parametrize(
    ("brier", "log_loss"),
    [
        (0.59, 1.01),
        (0.61, 0.99),
        (0.60, 1.00),
        (float("nan"), 0.99),
        (0.59, float("inf")),
    ],
)
def test_candidate_must_strictly_improve_both_finite_metrics(brier, log_loss):
    assert not promotion_allowed(
        candidate={"brier": brier, "log_loss": log_loss},
        champion={"brier": 0.60, "log_loss": 1.00},
        baseline={"brier": 0.62, "log_loss": 1.08},
    )


def test_local_compatibility_gate_uses_same_strict_both_metric_rule():
    assert not aprendizaje.supera(
        {"brier": 0.60, "log_loss": 0.98},
        {"brier": 0.60, "log_loss": 1.00},
    )


def test_candidate_is_evaluated_against_same_later_holdout():
    train, validation = build_chronological_split(make_examples(), set())

    decision = evaluate_candidate(
        train,
        validation,
        make_active({"local": 0.6, "visitante": 0.6}),
    )

    assert decision.approved is True
    assert decision.candidate_metrics.brier < decision.champion_metrics.brier
    assert decision.candidate_metrics.log_loss < decision.champion_metrics.log_loss
    assert decision.candidate_metrics.brier < decision.baseline_metrics.brier
    assert decision.candidate_metrics.log_loss < decision.baseline_metrics.log_loss
    assert decision.parameters["factores"] == {"local": 1.6, "visitante": 0.6}


def test_canonical_hash_sorts_parameters_and_both_evidence_id_sets():
    train_ids = [UUID(int=1), UUID(int=2)]
    validation_ids = [UUID(int=3), UUID(int=4)]
    first = canonical_candidate_hash(
        {"z": 1, "factores": {"visitante": 0.9, "local": 1.1}},
        train_ids,
        validation_ids,
        "abc123",
    )
    same = canonical_candidate_hash(
        {"factores": {"local": 1.1, "visitante": 0.9}, "z": 1},
        train_ids,
        validation_ids,
        "abc123",
    )

    assert first == same
    assert len(first) == 64
    assert first == canonical_candidate_hash(
        {"z": 1, "factores": {"visitante": 0.9, "local": 1.1}},
        list(reversed(train_ids)),
        list(reversed(validation_ids)),
        "abc123",
    )
    assert first != canonical_candidate_hash(
        {"z": 1, "factores": {"visitante": 0.9, "local": 1.1}},
        train_ids,
        validation_ids,
        "def456",
    )


class FakeTrainingRepository:
    def __init__(self, examples, active_model):
        self.examples = examples
        self.model = active_model
        self.consumed = set()
        self.events = []
        self.evaluations: list[CandidateEvaluation] = []
        self.promotion_outcome = "success"

    def recover_abandoned_training_evaluations(
        self, stale_after_seconds=900, limit=100
    ):
        self.events.append("recover_abandoned")
        assert stale_after_seconds == 900
        assert limit == 100
        return 0

    def training_examples(self, limit=1000):
        return self.examples[:limit]

    def consumed_validation_ids(self, limit=50000):
        return set(self.consumed)

    def active_model(self):
        return self.model

    def record_training_evaluation(self, evaluation):
        self.events.append("record")
        self.evaluations.append(evaluation)
        self.consumed.update(evaluation.validation_fixture_ids)
        return TrainingRun(
            id=RUN_ID,
            candidate_model_id=CANDIDATE_ID,
            status="candidate" if evaluation.approved else "rejected",
            train_fixture_ids=evaluation.train_fixture_ids,
            validation_fixture_ids=evaluation.validation_fixture_ids,
            parameter_hash=evaluation.parameter_hash,
            active_model_id=self.model.id,
        )

    def promote_model(self, candidate_id, current_id):
        self.events.append("promote")
        assert candidate_id == CANDIDATE_ID
        assert current_id == ACTIVE_ID
        if self.promotion_outcome == "uncommitted":
            raise TimeoutError("promotion response was uncertain")
        self.model = self.model.model_copy(
            update={"id": candidate_id, "version": "promoted", "state": "active"}
        )
        if self.promotion_outcome == "committed":
            raise TimeoutError("promotion committed before transport failed")
        return self.model

    def finalize_training_evaluation(self, run_id):
        self.events.append("finalize")
        assert run_id == RUN_ID
        if self.promotion_outcome == "committed":
            return TrainingRun(
                id=RUN_ID,
                candidate_model_id=CANDIDATE_ID,
                status="promoted",
                parameter_hash=self.evaluations[0].parameter_hash,
                active_model_id=CANDIDATE_ID,
            )
        return TrainingRun(
            id=RUN_ID,
            candidate_model_id=CANDIDATE_ID,
            status="failed",
            parameter_hash=self.evaluations[0].parameter_hash,
            active_model_id=ACTIVE_ID,
        )


def test_service_persists_immutable_candidate_before_promotion():
    repository = FakeTrainingRepository(
        make_examples(),
        make_active({"local": 0.6, "visitante": 0.6}),
    )

    result = TrainingService().run(repository, code_version="abc123")

    assert result.status == "promoted"
    assert repository.events == ["recover_abandoned", "record", "promote"]
    evaluation = repository.evaluations[0]
    assert evaluation.approved is True
    assert evaluation.version == f"live-fit-{evaluation.parameter_hash[:12]}"
    assert len(evaluation.train_fixture_ids) == 70
    assert len(evaluation.validation_fixture_ids) == 30


def test_rejected_candidate_and_run_consume_the_holdout_without_promotion():
    repository = FakeTrainingRepository(
        make_examples(),
        make_active({"local": 1.6, "visitante": 0.6}),
    )

    result = TrainingService().run(repository, code_version="abc123")

    assert result.status == "rejected"
    assert repository.events == ["recover_abandoned", "record"]
    assert repository.evaluations[0].approved is False
    assert set(result.validation_fixture_ids) <= repository.consumed

    second = TrainingService().run(repository, code_version="abc123")
    assert second.status == "collecting"
    assert repository.events == [
        "recover_abandoned",
        "record",
        "recover_abandoned",
    ]


def test_service_recognizes_promotion_committed_before_uncertain_response():
    repository = FakeTrainingRepository(
        make_examples(),
        make_active({"local": 0.6, "visitante": 0.6}),
    )
    repository.promotion_outcome = "committed"

    result = TrainingService().run(repository, code_version="abc123")

    assert result.status == "promoted"
    assert result.active_model_id == CANDIDATE_ID
    assert repository.events == [
        "recover_abandoned",
        "record",
        "promote",
        "finalize",
    ]


def test_service_finalizes_uncommitted_candidate_after_promotion_exception():
    repository = FakeTrainingRepository(
        make_examples(),
        make_active({"local": 0.6, "visitante": 0.6}),
    )
    repository.promotion_outcome = "uncommitted"

    result = TrainingService().run(repository, code_version="abc123")

    assert result.status == "failed"
    assert result.active_model_id == ACTIVE_ID
    assert repository.events == [
        "recover_abandoned",
        "record",
        "promote",
        "finalize",
    ]
