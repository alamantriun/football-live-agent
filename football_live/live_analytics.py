from __future__ import annotations

from collections.abc import Sequence
from math import exp, factorial, isfinite
from typing import Any


STAT_KEYS = (
    "possession", "shots", "shots_on_target", "corners",
    "yellow_cards", "red_cards", "expected_goals",
)
ACTIVITY_WEIGHTS = {
    "shots": 1.0,
    "shots_on_target": 2.0,
    "corners": 0.5,
    "expected_goals": 3.0,
}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def _non_negative_delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return max(0.0, current - previous)


def _trend_point(point: dict[str, Any], home: float | None, away: float | None) -> dict[str, Any]:
    return {
        "minute": point.get("minute"),
        "observed_at": point.get("provider_observed_at"),
        "home": home,
        "away": away,
    }


def _activity_series(points: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for index, current in enumerate(points):
        oldest = points[max(0, index - 4)]
        scores = {"home": 0.0, "away": 0.0}
        has_evidence = {"home": False, "away": False}
        for key, weight in ACTIVITY_WEIGHTS.items():
            current_pair = current.get("stats", {}).get(key, {})
            oldest_pair = oldest.get("stats", {}).get(key, {})
            for side in ("home", "away"):
                delta = _non_negative_delta(
                    _finite(current_pair.get(side)),
                    _finite(oldest_pair.get(side)),
                )
                if delta is not None:
                    has_evidence[side] = True
                    scores[side] += weight * delta

        total = scores["home"] + scores["away"]
        if not all(has_evidence.values()):
            home = None
            away = None
        elif total > 0.0:
            home = round(scores["home"] / total * 100.0, 2)
            away = round(100.0 - home, 2)
        else:
            possession = current.get("stats", {}).get("possession", {})
            home_possession = _finite(possession.get("home"))
            away_possession = _finite(possession.get("away"))
            possession_total = (
                home_possession + away_possession
                if home_possession is not None and away_possession is not None
                else 0.0
            )
            if possession_total > 0.0:
                home = round(home_possession / possession_total * 100.0, 2)
                away = round(100.0 - home, 2)
            else:
                home = None
                away = None
        series.append(_trend_point(current, home, away))
    return series


def _momentum_series(activity: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    for point in activity:
        home = _finite(point.get("home"))
        away = _finite(point.get("away"))
        if home is not None and away is not None:
            valid.append(point)

        if home is None or away is None or len(valid) < 4:
            momentum_home = None
            momentum_away = None
        else:
            previous = valid[-4]
            momentum_home = round(_clamp(50.0 + (home - previous["home"]) / 2.0), 2)
            momentum_away = round(_clamp(50.0 + (away - previous["away"]) / 2.0), 2)
        series.append(
            {
                "minute": point.get("minute"),
                "observed_at": point.get("observed_at"),
                "home": momentum_home,
                "away": momentum_away,
            }
        )
    return series


def _coverage(points: Sequence[dict[str, Any]]) -> dict[str, Any]:
    total = len(STAT_KEYS) * 2
    available = 0
    if points:
        stats = points[-1].get("stats", {})
        for key in STAT_KEYS:
            pair = stats.get(key, {})
            available += sum(_finite(pair.get(side)) is not None for side in ("home", "away"))
    return {
        "available": available,
        "total": total,
        "percent": round(available / total * 100.0, 2),
    }


def _latest_rates(points: Sequence[dict[str, Any]]) -> tuple[float, float] | None:
    if not points or points[-1].get("quality") in {"stale", "suspended"}:
        return None
    rates = points[-1].get("lambda_adjusted")
    if not isinstance(rates, dict):
        return None
    home = _finite(rates.get("home"))
    away = _finite(rates.get("away"))
    if home is None or away is None or home < 0.0 or away < 0.0:
        return None
    return home, away


def _poisson_probability(rate: float, goals: int) -> float:
    return exp(-rate) * rate**goals / factorial(goals)


def _remaining_goal_grid(home_rate: float, away_rate: float) -> list[tuple[int, int, float]]:
    home_probabilities = [_poisson_probability(home_rate, goals) for goals in range(11)]
    away_probabilities = [_poisson_probability(away_rate, goals) for goals in range(11)]
    return [
        (home_goals, away_goals, home_probability * away_probability)
        for home_goals, home_probability in enumerate(home_probabilities)
        for away_goals, away_probability in enumerate(away_probabilities)
    ]


def _current_score(point: dict[str, Any]) -> tuple[int, int]:
    home = _finite(point.get("score_home"))
    away = _finite(point.get("score_away"))
    return max(0, int(home or 0.0)), max(0, int(away or 0.0))


def _complementary_percentages(first_probability: float) -> tuple[float, float]:
    first = round(_clamp(first_probability, 0.0, 1.0) * 100.0, 2)
    return first, round(100.0 - first, 2)


def _next_goal(points: Sequence[dict[str, Any]]) -> dict[str, float] | None:
    rates = _latest_rates(points)
    if rates is None:
        return None
    home_rate, away_rate = rates
    total_rate = home_rate + away_rate
    no_goal = round(exp(-total_rate) * 100.0, 2)
    goal = round(100.0 - no_goal, 2)
    if total_rate == 0.0:
        home = 0.0
        away = 0.0
    else:
        home = round(goal * home_rate / total_rate, 2)
        away = round(goal - home, 2)
    return {"home": home, "none": no_goal, "away": away}


def _goal_markets(points: Sequence[dict[str, Any]]) -> dict[str, float] | None:
    rates = _latest_rates(points)
    if rates is None:
        return None
    home_rate, away_rate = rates
    score_home, score_away = _current_score(points[-1])
    grid = _remaining_goal_grid(home_rate, away_rate)

    under_probability = sum(
        probability
        for home_goals, away_goals, probability in grid
        if score_home + score_away + home_goals + away_goals <= 2
    )
    under, over = _complementary_percentages(under_probability)

    if score_home > 0 and score_away > 0:
        btts_probability = 1.0
    elif score_home > 0:
        btts_probability = 1.0 - _poisson_probability(away_rate, 0)
    elif score_away > 0:
        btts_probability = 1.0 - _poisson_probability(home_rate, 0)
    else:
        btts_probability = (
            (1.0 - _poisson_probability(home_rate, 0))
            * (1.0 - _poisson_probability(away_rate, 0))
        )
    btts_yes, btts_no = _complementary_percentages(btts_probability)
    return {
        "over_2_5": over,
        "under_2_5": under,
        "btts_yes": btts_yes,
        "btts_no": btts_no,
    }


def _total_goal_distribution(points: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rates = _latest_rates(points)
    if rates is None:
        return []
    home_rate, away_rate = rates
    score_home, score_away = _current_score(points[-1])
    probabilities = [0.0] * 7
    for home_goals, away_goals, probability in _remaining_goal_grid(home_rate, away_rate):
        final_total = score_home + score_away + home_goals + away_goals
        if final_total < 7:
            probabilities[final_total] += probability

    rows = [
        {"label": str(goals), "probability": round(probability * 100.0, 2)}
        for goals, probability in enumerate(probabilities)
    ]
    rounded_known = sum(row["probability"] for row in rows)
    rows.append({"label": "7+", "probability": round(max(0.0, 100.0 - rounded_known), 2)})
    return rows


def _scoreline_distribution(points: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rates = _latest_rates(points)
    if rates is None:
        return []
    home_rate, away_rate = rates
    score_home, score_away = _current_score(points[-1])
    rows = [
        (probability, score_home + home_goals, score_away + away_goals)
        for home_goals, away_goals, probability in _remaining_goal_grid(home_rate, away_rate)
    ]
    rows.sort(key=lambda row: (-row[0], row[1], row[2]))
    return [
        {"home": home, "away": away, "probability": round(probability * 100.0, 2)}
        for probability, home, away in rows[:5]
    ]


def build_live_analytics(points: Sequence[dict[str, Any]]) -> dict[str, Any]:
    bounded = list(points)[-90:]
    activity = _activity_series(bounded)
    return {
        "activity": activity,
        "momentum": _momentum_series(activity),
        "next_goal": _next_goal(bounded),
        "markets": _goal_markets(bounded),
        "total_goals": _total_goal_distribution(bounded),
        "scorelines": _scoreline_distribution(bounded),
        "coverage": _coverage(bounded),
    }
