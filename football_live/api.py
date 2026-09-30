from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hmac
import json
import logging
import re
from typing import Any, AsyncIterator, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .domain import DataStatus, StrictDomainModel
from .errors import ApiError, error_body
from .jobs import JobResult, run_collect, run_discover, run_settle, run_train
from .prediction_service import PredictionService
from .provider import ProviderClient, ProviderUnavailable
from .repository import RepositoryUnavailable
from .settings import Settings, get_settings
from .supabase_gateway import SupabaseGateway
from .training import TrainingService


logger = logging.getLogger(__name__)
_IDEMPOTENCY_KEY = re.compile(r"^[a-z]+:[A-Za-z0-9T:+._-]{1,80}$")
_MAX_JOB_BODY_BYTES = 1024


class PublicMatch(StrictDomainModel):
    public_id: UUID
    competition: str
    home_name: str
    away_name: str
    home_logo_url: str | None = None
    away_logo_url: str | None = None
    scheduled_at: AwareDatetime | None = None
    status: str
    minute: int | None = None
    score_home: int | None = None
    score_away: int | None = None
    data_status: DataStatus
    provider_observed_at: AwareDatetime | None = None
    collected_at: AwareDatetime | None = None
    probabilities: dict[str, float] | None = None
    explanation: dict[str, Any] | None = None
    model_version: str | None = None
    prediction_created_at: AwareDatetime | None = None
    updated_at: AwareDatetime


class LiveEnvelope(StrictDomainModel):
    request_id: UUID
    generated_at: AwareDatetime
    data_status: DataStatus
    model_version: str | None = None
    items: list[PublicMatch]


class MatchEnvelope(StrictDomainModel):
    request_id: UUID
    generated_at: AwareDatetime
    data_status: DataStatus
    model_version: str | None = None
    item: PublicMatch


class ModelStatus(StrictDomainModel):
    version: str
    train_size: int = Field(ge=0)
    validation_size: int = Field(ge=0)
    brier: float
    log_loss: float
    activated_at: AwareDatetime
    last_training_finished_at: AwareDatetime | None = None
    last_training_decision: str | None = None
    updated_at: AwareDatetime


class ModelStatusEnvelope(StrictDomainModel):
    request_id: UUID
    generated_at: AwareDatetime
    data_status: Literal["fresh"] = "fresh"
    model_version: str
    model: ModelStatus


class HealthEnvelope(StrictDomainModel):
    request_id: UUID
    generated_at: AwareDatetime
    status: Literal["ok"] = "ok"


class JobEnvelope(StrictDomainModel):
    request_id: UUID
    generated_at: AwareDatetime
    result: JobResult


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _request_id(request: Request) -> UUID:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, UUID) else uuid4()


def _json(
    status_code: int,
    content: StrictDomainModel,
    *,
    no_store: bool = False,
    public_cache: bool = False,
) -> JSONResponse:
    headers = None
    if no_store:
        headers = {"Cache-Control": "no-store"}
    elif public_cache:
        headers = {"Cache-Control": "public, max-age=0, s-maxage=15, stale-while-revalidate=30"}
    return JSONResponse(status_code=status_code, content=content.model_dump(mode="json"), headers=headers)


def _response_status(items: list[PublicMatch]) -> DataStatus:
    if not items:
        return DataStatus.SUSPENDED
    precedence = {
        DataStatus.FRESH: 0,
        DataStatus.DEGRADED: 1,
        DataStatus.STALE: 2,
        DataStatus.SUSPENDED: 3,
    }
    return max((item.data_status for item in items), key=precedence.__getitem__)


async def _require_job_request(
    request: Request,
    settings: Settings,
) -> str:
    x_cron_secret = request.headers.get("X-Cron-Secret")
    x_idempotency_key = request.headers.get("X-Idempotency-Key")
    secret = settings.cron_secret.get_secret_value() if settings.cron_secret else ""
    if not secret or x_cron_secret is None or not hmac.compare_digest(x_cron_secret, secret):
        raise ApiError(401, "unauthorized", "No autorizado.")
    if x_idempotency_key is None or not _IDEMPOTENCY_KEY.fullmatch(x_idempotency_key):
        raise ApiError(422, "invalid_request", "Solicitud interna inválida.")
    body = await request.body()
    if len(body) > _MAX_JOB_BODY_BYTES:
        raise ApiError(413, "payload_too_large", "Solicitud interna demasiado grande.")
    if body:
        try:
            payload = json.loads(body)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ApiError(422, "invalid_request", "Solicitud interna inválida.") from exc
        if not isinstance(payload, dict) or payload:
            raise ApiError(422, "invalid_request", "Solicitud interna inválida.")
    return x_idempotency_key


def create_app(
    settings: Settings,
    repository: Any,
    provider: Any,
    *,
    predictor: PredictionService | None = None,
    trainer: TrainingService | None = None,
) -> FastAPI:
    """Build the stateless app with injected dependencies for tests and production."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()

    app = FastAPI(
        debug=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    allowed_hosts = [host.strip() for host in settings.allowed_hosts.split(",") if host.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    prediction_service = predictor or PredictionService()
    training_service = trainer or TrainingService()

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        try:
            request.state.request_id = UUID(request.headers.get("X-Request-ID", ""))
        except ValueError:
            request.state.request_id = uuid4()
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error("api request failed: %s", type(exc).__name__)
            if isinstance(exc, (RepositoryUnavailable, ProviderUnavailable)):
                code, message, status_code = "unavailable", "Servicio temporalmente no disponible.", 503
            else:
                code, message, status_code = "internal_error", "No fue posible completar la solicitud.", 500
            response_status, content = error_body(status_code, code, message, _request_id(request))
            response = JSONResponse(
                status_code=response_status,
                content=content,
                headers={"Cache-Control": "no-store"},
            )
        response.headers["X-Request-ID"] = str(_request_id(request))
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        status_code, content = error_body(exc.status_code, exc.code, exc.message, _request_id(request))
        return JSONResponse(status_code=status_code, content=content, headers={"Cache-Control": "no-store"})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError) -> JSONResponse:
        status_code, content = error_body(422, "invalid_request", "Solicitud inválida.", _request_id(request))
        return JSONResponse(status_code=status_code, content=content, headers={"Cache-Control": "no-store"})

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("api request failed: %s", type(exc).__name__)
        if isinstance(exc, (RepositoryUnavailable, ProviderUnavailable)):
            code, message, status_code = "unavailable", "Servicio temporalmente no disponible.", 503
        else:
            code, message, status_code = "internal_error", "No fue posible completar la solicitud.", 500
        response_status, content = error_body(status_code, code, message, _request_id(request))
        return JSONResponse(status_code=response_status, content=content, headers={"Cache-Control": "no-store"})

    @app.get("/api/live", response_model=LiveEnvelope)
    async def live(request: Request, limit: int = 12, cursor: str | None = None) -> JSONResponse:
        if not 1 <= limit <= 50:
            raise ApiError(422, "invalid_request", "El límite debe estar entre 1 y 50.")
        items = [PublicMatch.model_validate(row) for row in repository.public_live(limit, cursor)]
        versions = {item.model_version for item in items if item.model_version}
        return _json(200, LiveEnvelope(
            request_id=_request_id(request),
            generated_at=_now(),
            data_status=_response_status(items),
            model_version=versions.pop() if len(versions) == 1 else None,
            items=items,
        ), public_cache=True)

    @app.get("/api/matches/{public_id}", response_model=MatchEnvelope)
    async def match(public_id: UUID, request: Request) -> JSONResponse:
        row = repository.public_match(public_id)
        if row is None:
            raise ApiError(404, "not_found", "Partido no encontrado.")
        item = PublicMatch.model_validate(row)
        return _json(200, MatchEnvelope(
            request_id=_request_id(request),
            generated_at=_now(),
            data_status=item.data_status,
            model_version=item.model_version,
            item=item,
        ), public_cache=True)

    @app.get("/api/model/status", response_model=ModelStatusEnvelope)
    async def model_status(request: Request) -> JSONResponse:
        raw = repository.public_model_status()
        if not raw:
            raise ApiError(503, "unavailable", "Modelo temporalmente no disponible.")
        model = ModelStatus.model_validate(raw)
        return _json(200, ModelStatusEnvelope(
            request_id=_request_id(request),
            generated_at=_now(),
            model_version=model.version,
            model=model,
        ), public_cache=True)

    @app.get("/api/health", response_model=HealthEnvelope)
    async def health(request: Request) -> JSONResponse:
        return _json(
            200,
            HealthEnvelope(request_id=_request_id(request), generated_at=_now()),
            public_cache=True,
        )

    async def run_job(request: Request, job_name: str) -> JSONResponse:
        key = await _require_job_request(request, settings)
        request_id = _request_id(request)
        jobs = {
            "discover": lambda: run_discover(repository, provider, request_id, key),
            "collect": lambda: run_collect(repository, provider, prediction_service, request_id, key),
            "settle": lambda: run_settle(repository, provider, request_id, key),
            "train": lambda: run_train(repository, training_service, request_id, key),
        }
        result = await jobs[job_name]()
        return _json(200, JobEnvelope(request_id=request_id, generated_at=_now(), result=result), no_store=True)

    @app.post("/api/jobs/discover", response_model=JobEnvelope)
    async def discover_job(request: Request) -> JSONResponse:
        return await run_job(request, "discover")

    @app.post("/api/jobs/collect", response_model=JobEnvelope)
    async def collect_job(request: Request) -> JSONResponse:
        return await run_job(request, "collect")

    @app.post("/api/jobs/settle", response_model=JobEnvelope)
    async def settle_job(request: Request) -> JSONResponse:
        return await run_job(request, "settle")

    @app.post("/api/jobs/train", response_model=JobEnvelope)
    async def train_job(request: Request) -> JSONResponse:
        return await run_job(request, "train")

    return app


def create_production_app() -> FastAPI:
    settings = get_settings()
    return create_app(settings, SupabaseGateway(settings), ProviderClient())
