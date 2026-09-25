from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any
from uuid import UUID

from .domain import Fixture, LiveSnapshot, ModelVersion, PredictionRecord
from .repository import RepositoryUnavailable
from .settings import Settings


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
            .limit(self._cap(limit, 100))
            .execute()
        )
        return [Fixture.model_validate(row) for row in self._rows(response)]

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
            .insert(payload)
            .select("id")
            .limit(1)
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
            .insert(payload)
            .select("id")
            .limit(1)
            .execute()
        )
        return self._inserted_id(response)

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

    def public_live(self, limit: int, cursor: str | None) -> list[dict]:
        def query() -> Any:
            builder = (
                self._client.schema("public")
                .table("live_matches")
                .select(PUBLIC_LIVE_SELECT)
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

    def claim_job(
        self, name: str, key: str, lease_seconds: int, request_id: UUID
    ) -> dict | None:
        # Task 2's RPC creates and returns the authoritative fencing token.
        del request_id
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
        return rows[0] if rows else None

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
