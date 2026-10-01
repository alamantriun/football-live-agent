from datetime import datetime, timedelta, timezone

from football_live.live_analytics import build_live_analytics


NOW = datetime(2026, 9, 30, 20, 0, tzinfo=timezone.utc)


def point(index, home_shots, away_shots, home_on_target, away_on_target):
    return {
        "minute": 50 + index,
        "provider_observed_at": NOW + timedelta(minutes=index),
        "score_home": 1,
        "score_away": 0,
        "stats": {
            "possession": {"home": 55.0, "away": 45.0},
            "shots": {"home": home_shots, "away": away_shots},
            "shots_on_target": {"home": home_on_target, "away": away_on_target},
            "corners": {"home": None, "away": None},
            "yellow_cards": {"home": 1, "away": 2},
            "red_cards": {"home": 0, "away": 0},
            "expected_goals": {"home": None, "away": None},
        },
        "probabilities": {"home": 60.0, "draw": 25.0, "away": 15.0},
        "lambda_adjusted": {"home": 1.4, "away": 0.6},
    }


def test_activity_uses_recent_cumulative_deltas_and_preserves_missing_signals():
    points = [
        point(0, 5, 4, 2, 1),
        point(1, 6, 4, 3, 1),
        point(2, 7, 5, 4, 1),
        point(3, 8, 5, 5, 1),
    ]

    analytics = build_live_analytics(points)

    assert analytics["activity"][-1]["home"] > analytics["activity"][-1]["away"]
    assert analytics["momentum"][-1]["home"] > 50.0
    assert analytics["coverage"] == {"available": 10, "total": 14, "percent": 71.43}


def test_zero_activity_falls_back_to_possession_but_absent_data_stays_null():
    same = [point(0, 5, 4, 2, 1), point(1, 5, 4, 2, 1)]
    analytics = build_live_analytics(same)
    assert analytics["activity"][-1]["home"] == 55.0
    assert analytics["activity"][-1]["away"] == 45.0

    missing = point(2, None, None, None, None)
    missing["stats"]["possession"] = {"home": None, "away": None}
    analytics = build_live_analytics([missing])
    assert analytics["activity"][-1]["home"] is None
    assert analytics["activity"][-1]["away"] is None


def test_activity_stays_null_when_only_one_side_has_weighted_evidence():
    points = [
        point(0, None, 4, None, 1),
        point(1, None, 5, None, 2),
    ]

    analytics = build_live_analytics(points)

    assert analytics["activity"][-1]["home"] is None
    assert analytics["activity"][-1]["away"] is None


def test_poisson_outputs_are_bounded_normalized_and_sorted():
    analytics = build_live_analytics([point(0, 5, 4, 2, 1)])

    assert round(sum(analytics["next_goal"].values()), 6) == 100.0
    assert round(analytics["markets"]["over_2_5"] + analytics["markets"]["under_2_5"], 6) == 100.0
    assert round(analytics["markets"]["btts_yes"] + analytics["markets"]["btts_no"], 6) == 100.0
    assert analytics["total_goals"][-1]["label"] == "7+"
    assert len(analytics["scorelines"]) == 5
    probabilities = [row["probability"] for row in analytics["scorelines"]]
    assert probabilities == sorted(probabilities, reverse=True)


def test_scorelines_select_top_five_before_rounding_probabilities():
    current = point(0, 5, 4, 2, 1)
    current["lambda_adjusted"] = {"home": 0.02, "away": 0.83}

    analytics = build_live_analytics([current])

    assert [(row["home"], row["away"]) for row in analytics["scorelines"]] == [
        (1, 0),
        (1, 1),
        (1, 2),
        (1, 3),
        (2, 0),
    ]


def test_invalid_or_absent_rates_hide_model_scenarios():
    invalid = point(0, 5, 4, 2, 1)
    invalid["lambda_adjusted"] = {"home": -1.0, "away": float("nan")}

    analytics = build_live_analytics([invalid])

    assert analytics["next_goal"] is None
    assert analytics["markets"] is None
    assert analytics["total_goals"] == []
    assert analytics["scorelines"] == []


def test_stale_quality_hides_model_scenarios():
    stale = point(0, 5, 4, 2, 1)
    stale["quality"] = "stale"

    analytics = build_live_analytics([stale])

    assert analytics["next_goal"] is None
    assert analytics["markets"] is None
    assert analytics["total_goals"] == []
    assert analytics["scorelines"] == []


def test_suspended_quality_hides_model_scenarios():
    suspended = point(0, 5, 4, 2, 1)
    suspended["quality"] = "suspended"

    analytics = build_live_analytics([suspended])

    assert analytics["next_goal"] is None
    assert analytics["markets"] is None
    assert analytics["total_goals"] == []
    assert analytics["scorelines"] == []
