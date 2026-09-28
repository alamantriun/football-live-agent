from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import unicodedata
from typing import Any

from calidad_vivo import classify_freshness
from simulador import correr

from .domain import DataStatus, HomeAwayFloat, LiveSnapshot, ModelVersion, PredictionRecord


_STAT_TO_LEGACY = {
    "possession": "posesion",
    "shots": "tiros",
    "shots_on_target": "tiros_puerta",
    "corners": "saques_esquina",
    "yellow_cards": "tarjetas_amarillas",
    "red_cards": "tarjetas_rojas",
    "fouls": "faltas",
    "offsides": "fueras_juego",
    "expected_goals": "xg",
}
_OPTIONAL_SIGNALS = frozenset(
    {"possession", "shots", "shots_on_target", "corners", "red_cards", "expected_goals"}
)
_CLOSED_STATUS_MARKERS = (
    "finished",
    "finalizado",
    "final",
    "ended",
    "disputed",
    "disputa",
    "cancelled",
    "cancelado",
    "abandoned",
    "abandonado",
    "postponed",
    "aplazado",
    "suspended",
    "suspendido",
)
_PROBABILITY_MAP = {
    "home": "prob_1x2_local",
    "draw": "prob_1x2_empate",
    "away": "prob_1x2_visitante",
    "next_goal_home": "prob_proximo_gol_local",
    "next_goal_away": "prob_proximo_gol_visitante",
    "no_more_goals": "prob_sin_mas_goles",
    "goal_next_10": "prob_gol_10_min",
    "goal_next_10_home": "prob_gol_10_local",
    "goal_next_10_away": "prob_gol_10_visitante",
    "over_1_5": "prob_over_1_5",
    "over_2_5": "prob_over_2_5",
    "over_3_5": "prob_over_3_5",
    "both_teams_score": "prob_btts",
}


def _plain_status(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _is_closed(snapshot: LiveSnapshot) -> bool:
    status = _plain_status(snapshot.sanitized_provider_data.get("status"))
    return any(marker in status for marker in _CLOSED_STATUS_MARKERS)


def _has_complete_optional_signals(snapshot: LiveSnapshot) -> bool:
    for name in _OPTIONAL_SIGNALS:
        pair = snapshot.stats.get(name)
        if pair is None or pair.home is None or pair.away is None:
            return False
    return True


def _canonical_stats(snapshot: LiveSnapshot) -> dict[str, dict[str, int | float | None]]:
    return {
        name: {"home": pair.home, "away": pair.away}
        for name, pair in snapshot.stats.items()
    }


def _legacy_event(snapshot: LiveSnapshot) -> dict[str, Any]:
    event: dict[str, Any] = {
        "timestamp": snapshot.provider_observed_at.isoformat(),
        "minuto": snapshot.minute,
        "marcador": {
            "local": snapshot.score_home,
            "visitante": snapshot.score_away,
        },
    }
    for canonical_name, legacy_name in _STAT_TO_LEGACY.items():
        pair = snapshot.stats.get(canonical_name)
        if pair is not None:
            event[legacy_name] = {"local": pair.home, "visitante": pair.away}
    return event


def _model_inputs(active_model: ModelVersion) -> tuple[float, float, dict[str, Any]]:
    parameters = active_model.parameters
    raw_factors = parameters.get("factores", parameters.get("factors", {}))
    if not isinstance(raw_factors, dict):
        raise ValueError("active model factors must be a mapping")
    factors = {
        "local": raw_factors.get("local", raw_factors.get("home", 1.0)),
        "visitante": raw_factors.get("visitante", raw_factors.get("away", 1.0)),
    }
    adjustment = {
        "aprobado": True,
        "version": active_model.version,
        "factores": factors,
    }
    prior_home = parameters.get("prior_local", parameters.get("prior_home", 1.5))
    prior_away = parameters.get("prior_visitante", parameters.get("prior_away", 1.2))
    return prior_home, prior_away, adjustment


class PredictionService:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def predict(
        self,
        snapshot: LiveSnapshot,
        active_model: ModelVersion,
    ) -> PredictionRecord:
        if snapshot.id is None:
            raise ValueError("persisted snapshot is required")
        if active_model.state != "active":
            raise ValueError("active model is required")

        now = self._clock()
        impossible = snapshot.minute is None or snapshot.minute > 120 or _is_closed(snapshot)
        if impossible or snapshot.quality == DataStatus.SUSPENDED:
            completeness: str = "suspended"
        elif snapshot.quality == DataStatus.STALE:
            completeness = "stale"
        elif snapshot.quality == DataStatus.FRESH and _has_complete_optional_signals(snapshot):
            completeness = "fresh"
        else:
            completeness = "degraded"
        quality = classify_freshness(
            snapshot.provider_observed_at,
            now,
            completeness,
        )

        if quality in ("stale", "suspended"):
            return self._sentinel(snapshot, active_model, quality, now)

        prior_home, prior_away, adjustment = _model_inputs(active_model)
        event = _legacy_event(snapshot)
        result = correr(
            [event],
            snapshot.minute,
            event["marcador"],
            prior_local=prior_home,
            prior_visitante=prior_away,
            ajuste=adjustment,
        )
        lambda_base = HomeAwayFloat(
            home=result["lambda_base"]["local"],
            away=result["lambda_base"]["visitante"],
        )
        lambda_adjusted = HomeAwayFloat(
            home=result["lambda_ajustada"]["local"],
            away=result["lambda_ajustada"]["visitante"],
        )
        warnings = list(result["advertencias"])
        if quality == "degraded":
            warnings.append(
                "Snapshot incompleto: las señales ausentes se conservaron como null."
            )
        explanation = {
            "model_version": active_model.version,
            "lambda_base": lambda_base.model_dump(),
            "lambda_adjusted": lambda_adjusted.model_dump(),
            "inputs_used": {
                "minute": snapshot.minute,
                "score": {"home": snapshot.score_home, "away": snapshot.score_away},
                "stats": _canonical_stats(snapshot),
                "provider_observed_at": snapshot.provider_observed_at.isoformat(),
                "prior": {"home": prior_home, "away": prior_away},
                "factors": {
                    "home": result["factores_aplicados"]["local"],
                    "away": result["factores_aplicados"]["visitante"],
                },
            },
            "warnings": warnings,
            "retain_previous": False,
            "persist": True,
        }
        return PredictionRecord(
            fixture_id=snapshot.fixture_id,
            snapshot_id=snapshot.id,
            model_version_id=active_model.id,
            lambda_base=lambda_base,
            lambda_adjusted=lambda_adjusted,
            probabilities={
                canonical: result[source]
                for canonical, source in _PROBABILITY_MAP.items()
            },
            explanation=explanation,
            quality=quality,
            created_at=now,
        )

    @staticmethod
    def _sentinel(
        snapshot: LiveSnapshot,
        active_model: ModelVersion,
        quality: str,
        now: datetime,
    ) -> PredictionRecord:
        stale = quality == "stale"
        zero_lambda = HomeAwayFloat(home=0.0, away=0.0)
        reason = (
            "Retain previous persisted prediction; do not persist this stale sentinel."
            if stale
            else "Prediction suspended; no probabilities were derived or persisted."
        )
        explanation = {
            "model_version": active_model.version,
            "lambda_base": zero_lambda.model_dump(),
            "lambda_adjusted": zero_lambda.model_dump(),
            "inputs_used": {},
            "warnings": [reason],
            "retain_previous": stale,
            "persist": False,
            "reason": reason,
        }
        return PredictionRecord(
            fixture_id=snapshot.fixture_id,
            snapshot_id=snapshot.id,
            model_version_id=active_model.id,
            lambda_base=zero_lambda,
            lambda_adjusted=zero_lambda,
            probabilities={},
            explanation=explanation,
            quality=quality,
            created_at=now,
        )
