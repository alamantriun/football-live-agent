from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from math import isfinite
from typing import Any
from uuid import UUID

from .domain import Fixture, JobClaim, LiveSnapshot, ModelVersion, PredictionRecord
from .repository import RepositoryUnavailable
from .settings import Settings
from .training import CandidateEvaluation, TrainingExample, TrainingRun


PUBLIC_ERROR = "El repositorio no está disponible temporalmente."
FIXTURE_SELECT = (
    "id,public_id,provider,provider_fixture_id,competition,home_name,away_name,"
    "home_logo_url,away_logo_url,scheduled_at,status,final_home,final_away,"
    "discovered_at,updated_at,finished_at"
)
MODEL_SELECT = (
    "id,version,state,parameters,parameter_hash,code_version,previous_model_id,"
    "train_size,validation_size,brier,log_loss,created_at,activated_at"
)
PUBLIC_LIVE_SELECT = (
    "public_id,competition,home_name,away_name,home_logo_url,away_logo_url,"
    "scheduled_at,status,minute,score_home,score_away,data_status,"
    "provider_observed_at,collected_at,probabilities,explanation,model_version,"
    "prediction_created_at,updated_at"
)
PUBLIC_MODEL_SELECT = (
    "version,train_size,validation_size,brier,log_loss,activated_at,"
    "last_training_finished_at,last_training_decision,updated_at"
)
HISTORY_FIXTURE_SELECT = "id,public_id,home_name,away_name"
HISTORY_SNAPSHOT_SELECT = (
    "id,minute,score_home,score_away,normalized_stats,"
    "provider_observed_at,collected_at,quality"
)
HISTORY_STAT_KEYS = (
    "possession",
    "shots",
    "shots_on_target",
    "corners",
    "yellow_cards",
    "red_cards",
    "expected_goals",
)
TRAINING_EXAMPLE_SELECT = (
    "fixture_id,first_observed_at,outcome_confirmed_at,final_home,"
    "final_away,observations"
)


def _create_client(url: str, key: str) -> Any:
    from supabase import create_client

    return create_client(url, key)


class SupabaseGateway:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[str, str], Any] = _create_client,
    ) -> None:
        if settings.supabase_url is None or settings.supabase_service_role_key is None:
            raise ValueError("Supabase server configuration is required")
        key = settings.supabase_service_role_key.get_secret_value()
        if not key.strip():
            raise ValueError("Supabase server configuration is required")
        try:
            self._client = client_factory(str(settings.supabase_url), key)
        except Exception as exc:
            raise RepositoryUnavailable(PUBLIC_ERROR) from exc

    @staticmethod
    def _rows(response: Any) -> list[dict]:
        data = getattr(response, "data", None)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []

    @staticmethod
    def _cap(limit: int, maximum: int) -> int:
        return max(1, min(int(limit), maximum))

    def _execute(self, operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except RepositoryUnavailable:
            raise
        except Exception as exc:
            raise RepositoryUnavailable(PUBLIC_ERROR) from exc

    def _inserted_id(self, response: Any) -> UUID:
        try:
            return UUID(str(self._rows(response)[0]["id"]))
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise RepositoryUnavailable(PUBLIC_ERROR) from exc

    def upsert_fixtures(self, fixtures: Sequence[Fixture]) -> int:
        if not fixtures:
            return 0
        payload = [self._fixture_payload(fixture) for fixture in fixtures]
        response = self._execute(
            lambda: self._client.schema("private")
            .table("fixtures")
            .upsert(payload, on_conflict="provider,provider_fixture_id")
            .select("id")
            .execute()
        )
        return len(self._rows(response))

    def sync_live_fixture_logos(self, fixture: Fixture) -> None:
        if fixture.public_id is None:
            raise ValueError("persisted public fixture is required")
        payload = {
            "home_logo_url": fixture.home_logo_url,
            "away_logo_url": fixture.away_logo_url,
        }
        self._execute(
            lambda: self._client.schema("public")
            .table("live_match_projection")
            .update(payload)
            .eq("public_id", str(fixture.public_id))
            .execute()
        )

    @staticmethod
    def _fixture_payload(fixture: Fixture) -> dict[str, Any]:
        dumped = fixture.model_dump(mode="json")
        fields = (
            "provider",
            "provider_fixture_id",
            "competition",
            "home_name",
            "away_name",
            "home_logo_url",
            "away_logo_url",
            "scheduled_at",
            "status",
            "final_home",
            "final_away",
            "finished_at",
        )
        return {field: dumped[field] for field in fields}

    def active_fixtures(self, limit: int = 12) -> list[Fixture]:
        response = self._execute(
            lambda: self._client.schema("private")
            .table("fixtures")
            .select(FIXTURE_SELECT)
            .eq("status", "live")
            .limit(self._cap(limit, 12))
            .execute()
        )
        return [Fixture.model_validate(row) for row in self._rows(response)]

    def unfinished_fixtures(self, limit: int = 30) -> list[Fixture]:
        response = self._execute(
            lambda: self._client.schema("private")
            .table("fixtures")
            .select(FIXTURE_SELECT)
            .is_("finished_at", "null")
            .order("scheduled_at")
            .limit(self._cap(limit, 30))
            .execute()
        )
        return [Fixture.model_validate(row) for row in self._rows(response)]

    def store_confirmed_outcome(
        self,
        fixture: Fixture,
        home_score: int,
        away_score: int,
        confirmed_at: datetime,
        source: str,
    ) -> None:
        if fixture.id is None:
            raise ValueError("persisted fixture is required")
        payload = {
            "fixture_id": str(fixture.id),
            "home_score": home_score,
            "away_score": away_score,
            "source": source,
            "confirmed": True,
            "confirmed_at": confirmed_at.isoformat(),
        }
        self._execute(
            lambda: self._client.schema("private")
            .table("outcomes")
            .upsert(payload, on_conflict="fixture_id")
            .select("fixture_id")
            .execute()
        )
        closed = fixture.model_copy(
            update={
                "status": "finished",
                "final_home": home_score,
                "final_away": away_score,
                "finished_at": confirmed_at,
            }
        )
        self.upsert_fixtures([closed])

    def flag_fixture_review(self, fixture: Fixture, reason: str) -> None:
        if fixture.id is None:
            raise ValueError("persisted fixture is required")
        if reason != "provider_score_correction":
            raise ValueError("unsupported review reason")
        self.upsert_fixtures([fixture.model_copy(update={"status": "review"})])

    def store_snapshot(self, snapshot: LiveSnapshot) -> UUID:
        dumped = snapshot.model_dump(mode="json")
        payload = {
            "fixture_id": dumped["fixture_id"],
            "minute": dumped["minute"],
            "score_home": dumped["score_home"],
            "score_away": dumped["score_away"],
            "normalized_stats": dumped["stats"],
            "sanitized_provider_data": dumped["sanitized_provider_data"],
            "provider_observed_at": dumped["provider_observed_at"],
            "quality": dumped["quality"],
            "observation_bucket": dumped["observation_bucket"],
        }
        response = self._execute(
            lambda: self._client.schema("private")
            .table("live_snapshots")
            .upsert(payload, on_conflict="fixture_id,observation_bucket")
            .select("id")
            .execute()
        )
        return self._inserted_id(response)

    def store_prediction(self, prediction: PredictionRecord) -> UUID:
        dumped = prediction.model_dump(mode="json")
        fields = (
            "fixture_id",
            "snapshot_id",
            "model_version_id",
            "lambda_base",
            "lambda_adjusted",
            "probabilities",
            "explanation",
            "quality",
        )
        payload = {field: dumped[field] for field in fields}
        response = self._execute(
            lambda: self._client.schema("private")
            .table("predictions")
            .upsert(payload, on_conflict="snapshot_id,model_version_id")
            .select("id")
            .execute()
        )
        return self._inserted_id(response)

    def publish_live_prediction(
        self,
        fixture: Fixture,
        snapshot: LiveSnapshot,
        prediction: PredictionRecord,
        model: ModelVersion,
    ) -> None:
        if fixture.public_id is None or fixture.id is None or snapshot.id is None:
            raise ValueError("persisted fixture and snapshot are required")
        if (
            snapshot.fixture_id != fixture.id
            or prediction.fixture_id != fixture.id
            or prediction.snapshot_id != snapshot.id
            or prediction.model_version_id != model.id
        ):
            raise ValueError("projection inputs must describe the same prediction")

        fixture_data = fixture.model_dump(mode="json")
        snapshot_data = snapshot.model_dump(mode="json")
        prediction_data = prediction.model_dump(mode="json")
        payload = {
            "public_id": fixture_data["public_id"],
            "competition": fixture_data["competition"],
            "home_name": fixture_data["home_name"],
            "away_name": fixture_data["away_name"],
            "home_logo_url": fixture_data["home_logo_url"],
            "away_logo_url": fixture_data["away_logo_url"],
            "scheduled_at": fixture_data["scheduled_at"],
            "status": fixture_data["status"],
            "minute": snapshot_data["minute"],
            "score_home": snapshot_data["score_home"],
            "score_away": snapshot_data["score_away"],
            "data_status": prediction_data["quality"],
            "provider_observed_at": snapshot_data["provider_observed_at"],
            "collected_at": snapshot_data["collected_at"],
            "probabilities": prediction_data["probabilities"],
            "explanation": prediction_data["explanation"],
            "model_version": model.version,
            "prediction_created_at": prediction_data["created_at"],
            "updated_at": (
                prediction_data["created_at"]
                or snapshot_data["provider_observed_at"]
            ),
        }
        self._execute(
            lambda: self._client.schema("public")
            .table("live_match_projection")
            .upsert(payload, on_conflict="public_id")
            .execute()
        )

    def active_model(self) -> ModelVersion:
        response = self._execute(
            lambda: self._client.schema("private")
            .table("model_versions")
            .select(MODEL_SELECT)
            .eq("state", "active")
            .limit(1)
            .execute()
        )
        rows = self._rows(response)
        if not rows:
            raise LookupError("No active model is available")
        return ModelVersion.model_validate(rows[0])

    def training_examples(self, limit: int = 1000) -> list[TrainingExample]:
        response = self._execute(
            lambda: self._client.schema("private")
            .table("training_examples")
            .select(TRAINING_EXAMPLE_SELECT)
            .order("first_observed_at", desc=True)
            .limit(self._cap(limit, 1000))
            .execute()
        )
        return [TrainingExample.model_validate(row) for row in self._rows(response)]

    def consumed_validation_ids(self, limit: int = 50000) -> set[UUID]:
        bounded_limit = self._cap(limit, 50000)
        rows: list[dict] = []
        offset = 0
        page_size = 1000
        while offset < bounded_limit:
            requested = min(page_size, bounded_limit - offset)
            response = self._execute(
                lambda offset=offset, requested=requested: self._client.schema(
                    "private"
                )
                .table("consumed_validation_fixtures")
                .select("fixture_id")
                .order("fixture_id")
                .range(offset, offset + requested - 1)
                .execute()
            )
            page = self._rows(response)
            rows.extend(page)
            if len(page) < requested:
                break
            offset += requested
        else:
            response = self._execute(
                lambda: self._client.schema("private")
                .table("consumed_validation_fixtures")
                .select("fixture_id")
                .order("fixture_id")
                .range(bounded_limit, bounded_limit)
                .execute()
            )
            if self._rows(response):
                raise RepositoryUnavailable(PUBLIC_ERROR)
        try:
            return {UUID(str(row["fixture_id"])) for row in rows}
        except (KeyError, TypeError, ValueError) as exc:
            raise RepositoryUnavailable(PUBLIC_ERROR) from exc

    def record_training_evaluation(
        self, evaluation: CandidateEvaluation, run_id: UUID
    ) -> TrainingRun:
        metrics = {
            "candidate": evaluation.candidate_metrics.model_dump(),
            "champion": evaluation.champion_metrics.model_dump(),
            "baseline": evaluation.baseline_metrics.model_dump(),
        }
        response = self._execute(
            lambda: self._client.schema("private")
            .rpc(
                "record_training_evaluation",
                {
                    "p_run_id": str(run_id),
                    "p_parameters": evaluation.parameters,
                    "p_code_version": evaluation.code_version,
                    "p_previous_model_id": str(evaluation.previous_model_id),
                    "p_train_fixture_ids": [
                        str(item) for item in evaluation.train_fixture_ids
                    ],
                    "p_validation_fixture_ids": [
                        str(item) for item in evaluation.validation_fixture_ids
                    ],
                    "p_metrics": metrics,
                    "p_approved": evaluation.approved,
                    "p_reason": evaluation.reason,
                },
            )
            .execute()
        )
        rows = self._rows(response)
        try:
            run = self._training_run(
                rows[0],
                train_fixture_ids=evaluation.train_fixture_ids,
                validation_fixture_ids=evaluation.validation_fixture_ids,
                active_model_id=evaluation.previous_model_id,
            )
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise RepositoryUnavailable(PUBLIC_ERROR) from exc
        if run.status == "failed":
            return run.model_copy(update={"active_model_id": self.active_model().id})
        return run

    def promote_model(self, candidate_id: UUID, current_id: UUID) -> ModelVersion:
        response = self._execute(
            lambda: self._client.schema("private")
            .rpc(
                "promote_model",
                {
                    "p_candidate_id": str(candidate_id),
                    "p_current_id": str(current_id),
                },
            )
            .execute()
        )
        rows = self._rows(response)
        if not rows:
            raise RepositoryUnavailable(PUBLIC_ERROR)
        return ModelVersion.model_validate(rows[0])

    @staticmethod
    def _training_run(
        row: dict,
        *,
        train_fixture_ids: tuple[UUID, ...] = (),
        validation_fixture_ids: tuple[UUID, ...] = (),
        active_model_id: UUID | None = None,
    ) -> TrainingRun:
        status_by_decision = {
            "running": "candidate",
            "rejected": "rejected",
            "failed": "failed",
            "succeeded": "promoted",
        }
        status = status_by_decision[row["decision"]]
        raw_candidate_model_id = row["candidate_model_id"]
        candidate_model_id = (
            UUID(str(raw_candidate_model_id))
            if raw_candidate_model_id is not None
            else None
        )
        if not train_fixture_ids:
            train_fixture_ids = tuple(
                UUID(str(item)) for item in row.get("train_fixture_ids") or ()
            )
        if not validation_fixture_ids:
            validation_fixture_ids = tuple(
                UUID(str(item))
                for item in row.get("validation_fixture_ids") or ()
            )
        if status == "promoted":
            if candidate_model_id is None:
                raise ValueError("promoted run requires a candidate model")
            active_model_id = candidate_model_id
        return TrainingRun(
            id=row["id"],
            candidate_model_id=candidate_model_id,
            status=status,
            train_fixture_ids=train_fixture_ids,
            validation_fixture_ids=validation_fixture_ids,
            parameter_hash=row["parameter_hash"],
            active_model_id=active_model_id,
        )

    def finalize_training_evaluation(self, run_id: UUID) -> TrainingRun:
        response = self._execute(
            lambda: self._client.schema("private")
            .rpc(
                "finalize_training_evaluation",
                {"p_run_id": str(run_id)},
            )
            .execute()
        )
        rows = self._rows(response)
        try:
            run = self._training_run(rows[0])
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise RepositoryUnavailable(PUBLIC_ERROR) from exc
        if run.status in {"failed", "rejected"}:
            return run.model_copy(update={"active_model_id": self.active_model().id})
        return run

    def recover_abandoned_training_evaluations(
        self, stale_after_seconds: int = 900, limit: int = 100
    ) -> int:
        response = self._execute(
            lambda: self._client.schema("private")
            .rpc(
                "recover_abandoned_training_evaluations",
                {
                    "p_stale_after_seconds": stale_after_seconds,
                    "p_limit": limit,
                },
            )
            .execute()
        )
        rows = self._rows(response)
        try:
            for row in rows:
                self._training_run(row)
        except (KeyError, TypeError, ValueError) as exc:
            raise RepositoryUnavailable(PUBLIC_ERROR) from exc
        return len(rows)

    def public_live(self, limit: int, cursor: str | None) -> list[dict]:
        def query() -> Any:
            builder = (
                self._client.schema("public")
                .table("live_matches")
                .select(PUBLIC_LIVE_SELECT)
                .eq("status", "live")
                .order("updated_at", desc=True)
            )
            if cursor is not None:
                builder = builder.lt("updated_at", cursor)
            return builder.limit(self._cap(limit, 50)).execute()

        return self._rows(self._execute(query))

    def public_match(self, public_id: UUID) -> dict | None:
        response = self._execute(
            lambda: self._client.schema("public")
            .table("live_matches")
            .select(PUBLIC_LIVE_SELECT)
            .eq("public_id", str(public_id))
            .limit(1)
            .execute()
        )
        rows = self._rows(response)
        return rows[0] if rows else None

    def public_match_history(
        self, public_id: UUID, limit: int = 90
    ) -> dict | None:
        fixture_response = self._execute(
            lambda: self._client.schema("private")
            .table("fixtures")
            .select(HISTORY_FIXTURE_SELECT)
            .eq("public_id", str(public_id))
            .limit(1)
            .execute()
        )
        fixtures = self._rows(fixture_response)
        if not fixtures:
            return None

        try:
            fixture = fixtures[0]
            fixture_id = str(UUID(str(fixture["id"])))
            result = {
                "public_id": fixture["public_id"],
                "home_name": fixture["home_name"],
                "away_name": fixture["away_name"],
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise RepositoryUnavailable(PUBLIC_ERROR) from exc

        bounded_limit = self._cap(limit, 90)
        snapshot_response = self._execute(
            lambda: self._client.schema("private")
            .table("live_snapshots")
            .select(HISTORY_SNAPSHOT_SELECT)
            .eq("fixture_id", fixture_id)
            .order("provider_observed_at", desc=True)
            .limit(bounded_limit)
            .execute()
        )
        prediction_response = self._execute(
            lambda: self._client.schema("private")
            .rpc(
                "match_history_predictions",
                {"p_fixture_id": fixture_id, "p_limit": bounded_limit},
            )
            .execute()
        )

        try:
            predictions: dict[str, dict[str, Any]] = {}
            for row in self._rows(prediction_response):
                snapshot_id = str(UUID(str(row["snapshot_id"])))
                version = row["model_version"]
                if not isinstance(version, str) or not version:
                    raise TypeError("malformed model version")
                predictions[snapshot_id] = {
                    "probabilities": self._history_mapping(
                        row["probabilities"], ("home", "draw", "away")
                    ),
                    "lambda_adjusted": self._history_mapping(
                        row["lambda_adjusted"], ("home", "away")
                    ),
                    "prediction_created_at": row["created_at"],
                    "model_version": version,
                }

            newest_points: list[dict[str, Any]] = []
            model_version: str | None = None
            for snapshot in self._rows(snapshot_response):
                snapshot_id = str(UUID(str(snapshot["id"])))
                prediction = predictions.get(snapshot_id)
                if model_version is None and prediction is not None:
                    model_version = prediction["model_version"]
                newest_points.append(
                    {
                        "minute": snapshot["minute"],
                        "score_home": snapshot["score_home"],
                        "score_away": snapshot["score_away"],
                        "stats": self._history_stats(snapshot["normalized_stats"]),
                        "provider_observed_at": snapshot["provider_observed_at"],
                        "collected_at": snapshot["collected_at"],
                        "quality": snapshot["quality"],
                        "probabilities": (
                            prediction["probabilities"] if prediction else None
                        ),
                        "lambda_adjusted": (
                            prediction["lambda_adjusted"] if prediction else None
                        ),
                        "prediction_created_at": (
                            prediction["prediction_created_at"]
                            if prediction
                            else None
                        ),
                    }
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise RepositoryUnavailable(PUBLIC_ERROR) from exc

        return result | {
            "model_version": model_version,
            "points": list(reversed(newest_points)),
        }

    @staticmethod
    def _history_mapping(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise TypeError("malformed history mapping")
        return {key: value.get(key) for key in keys}

    @classmethod
    def _history_stats(cls, value: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(value, dict):
            raise TypeError("malformed history stats")
        stats: dict[str, dict[str, Any]] = {}
        for key in HISTORY_STAT_KEYS:
            pair = value.get(key, {})
            if not isinstance(pair, dict):
                raise TypeError("malformed history stat pair")
            stats[key] = {
                side: cls._history_stat_leaf(pair.get(side))
                for side in ("home", "away")
            }
        return stats

    @staticmethod
    def _history_stat_leaf(value: Any) -> int | float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("malformed history stat leaf")
        try:
            finite = isfinite(value)
        except OverflowError as exc:
            raise TypeError("malformed history stat leaf") from exc
        if not finite:
            raise TypeError("malformed history stat leaf")
        return value

    def public_model_status(self) -> dict:
        response = self._execute(
            lambda: self._client.schema("public")
            .table("model_status")
            .select(PUBLIC_MODEL_SELECT)
            .limit(1)
            .execute()
        )
        rows = self._rows(response)
        return rows[0] if rows else {}

    def claim_job(self, name: str, key: str, lease_seconds: int) -> JobClaim | None:
        response = self._execute(
            lambda: self._client.schema("private")
            .rpc(
                "claim_job",
                {
                    "p_job_name": name,
                    "p_idempotency_key": key,
                    "p_lease_seconds": lease_seconds,
                },
            )
            .execute()
        )
        rows = self._rows(response)
        if not rows:
            return None
        return JobClaim(run_id=rows[0]["id"], request_id=rows[0]["request_id"])

    def finish_job(
        self,
        run_id: UUID,
        request_id: UUID,
        state: str,
        counters: dict,
        error: str | None = None,
    ) -> None:
        self._execute(
            lambda: self._client.schema("private")
            .rpc(
                "finish_job",
                {
                    "p_run_id": str(run_id),
                    "p_request_id": str(request_id),
                    "p_state": state,
                    "p_counters": counters,
                    "p_sanitized_error": error,
                },
            )
            .execute()
        )
