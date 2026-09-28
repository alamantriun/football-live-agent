from __future__ import annotations

import asyncio
import json
import math
import random
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import AwareDatetime, Field

from football_live.domain import (
    DataStatus,
    HomeAwayStat,
    MatchMinute,
    Score,
    StrictDomainModel,
)


PROVIDER_BASE_URL = "https://webws.365scores.com/web/"
LIVE_LIST_PATH = "games/allscores/"
GAME_PATH = "game/"
GAME_STATS_PATH = "game/stats/"

MAX_ATTEMPTS = 3
MAX_RESPONSE_BYTES = 1_000_000
MAX_CONNECTIONS = 10
MAX_KEEPALIVE_CONNECTIONS = 5
TOTAL_TIMEOUT_SECONDS = 12.0
CONNECT_TIMEOUT_SECONDS = 5.0

_FIXTURE_ID = re.compile(r"[0-9]{1,20}\Z")
_NUMBER = re.compile(r"[0-9]+(?:[.,][0-9]+)?%?\Z")
_ALLOWED_PATHS = frozenset({LIVE_LIST_PATH, GAME_PATH, GAME_STATS_PATH})
_COMMON_PARAMS = {
    "appTypeId": "5",
    "langId": "29",
    "timezoneName": "America/Mexico_City",
    "userCountryId": "29",
}
_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Origin": "https://www.365scores.com",
    "Referer": "https://www.365scores.com/es",
    "User-Agent": "football-live-agent/1.0",
}

_STAT_NAMES = {
    "posesión": "possession",
    "possession": "possession",
    "total remates": "shots",
    "total shots": "shots",
    "remates al arco": "shots_on_target",
    "shots on target": "shots_on_target",
    "saques de esquina": "corners",
    "corner kicks": "corners",
    "tarjetas amarillas": "yellow_cards",
    "yellow cards": "yellow_cards",
    "tarjetas rojas": "red_cards",
    "red cards": "red_cards",
    "faltas": "fouls",
    "fouls": "fouls",
    "fueras de juego": "offsides",
    "offsides": "offsides",
    "expected goals": "expected_goals",
    "goles esperados": "expected_goals",
    "xg": "expected_goals",
}
_COUNT_STATS = {
    "shots",
    "shots_on_target",
    "corners",
    "yellow_cards",
    "red_cards",
    "fouls",
    "offsides",
}
_LEGACY_STAT_NAMES = {
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


class ProviderUnavailable(RuntimeError):
    """A sanitized provider failure safe to expose at service boundaries."""


class ProviderFixture(StrictDomainModel):
    provider_fixture_id: str = Field(pattern=r"^[0-9]{1,20}$")
    competition: str = Field(min_length=1)
    home_name: str = Field(min_length=1)
    away_name: str = Field(min_length=1)
    home_logo_url: str | None = None
    away_logo_url: str | None = None
    scheduled_at: AwareDatetime | None = None
    status: str = Field(min_length=1)


def _empty_pair() -> HomeAwayStat:
    return HomeAwayStat(home=None, away=None)


class ProviderStats(StrictDomainModel):
    possession: HomeAwayStat = Field(default_factory=_empty_pair)
    shots: HomeAwayStat = Field(default_factory=_empty_pair)
    shots_on_target: HomeAwayStat = Field(default_factory=_empty_pair)
    corners: HomeAwayStat = Field(default_factory=_empty_pair)
    yellow_cards: HomeAwayStat = Field(default_factory=_empty_pair)
    red_cards: HomeAwayStat = Field(default_factory=_empty_pair)
    fouls: HomeAwayStat = Field(default_factory=_empty_pair)
    offsides: HomeAwayStat = Field(default_factory=_empty_pair)
    expected_goals: HomeAwayStat = Field(default_factory=_empty_pair)


class ProviderSnapshot(StrictDomainModel):
    provider_fixture_id: str = Field(pattern=r"^[0-9]{1,20}$")
    home_name: str = Field(min_length=1)
    away_name: str = Field(min_length=1)
    minute: MatchMinute | None
    score_home: Score
    score_away: Score
    stats: ProviderStats
    provider_observed_at: AwareDatetime
    quality: DataStatus
    sanitized_provider_data: dict[str, Any] = Field(default_factory=dict)


def parse_number(value: Any) -> float | None:
    """Parse a provider number without replacing missing/invalid data with zero."""
    if value is None or isinstance(value, bool):
        return None
    raw = str(value).strip()
    if not _NUMBER.fullmatch(raw):
        return None
    parsed = float(raw.rstrip("%").replace(",", "."))
    return parsed if math.isfinite(parsed) else None


def parse_minute(game: dict[str, Any]) -> float | None:
    precise = game.get("preciseGameTime") or {}
    if isinstance(precise, dict):
        parsed = parse_number(precise.get("minutes"))
        if parsed is not None:
            return parsed
    parsed = parse_number(game.get("gameTime"))
    if parsed is not None:
        return parsed
    match = re.search(r"([0-9]+)", str(game.get("gameTimeDisplay") or ""))
    return float(match.group(1)) if match else None


def parse_score(game: dict[str, Any]) -> tuple[int | None, int | None]:
    home = game.get("homeCompetitor") or {}
    away = game.get("awayCompetitor") or {}
    home_score = parse_number(home.get("score"))
    away_score = parse_number(away.get("score"))
    if (
        home_score is None
        or away_score is None
        or home_score < 0
        or away_score < 0
        or not home_score.is_integer()
        or not away_score.is_integer()
    ):
        return None, None
    return int(home_score), int(away_score)


def parse_statistics(
    stats_data: dict[str, Any] | None,
    home_id: int,
    away_id: int,
) -> dict[str, dict[str, int | float | None]]:
    result = {
        name: {"home": None, "away": None}
        for name in _LEGACY_STAT_NAMES
    }
    if not isinstance(stats_data, dict):
        return result

    for item in stats_data.get("statistics") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("name") or item.get("categoryName") or "").lower().strip()
        stat_name = _STAT_NAMES.get(label)
        if stat_name is None:
            continue
        competitor_id = item.get("competitorId")
        side = "home" if competitor_id == home_id else "away" if competitor_id == away_id else None
        value = parse_number(item.get("value"))
        if side is None or value is None:
            continue
        result[stat_name][side] = int(value) if stat_name in _COUNT_STATS else value
    return result


def parse_statistics_legacy(
    stats_data: dict[str, Any] | None,
    home_id: int,
    away_id: int,
) -> dict[str, dict[str, int | float | None]]:
    canonical = parse_statistics(stats_data, home_id, away_id)
    return {
        legacy_name: {
            "local": canonical[canonical_name]["home"],
            "visitante": canonical[canonical_name]["away"],
        }
        for canonical_name, legacy_name in _LEGACY_STAT_NAMES.items()
    }


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class ProviderAdapter:
    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def parse_live_list(self, payload: dict[str, Any]) -> list[ProviderFixture]:
        if not isinstance(payload, dict) or not isinstance(payload.get("games"), list):
            raise ProviderUnavailable("provider unavailable")
        fixtures: list[ProviderFixture] = []
        for game in payload["games"]:
            if not isinstance(game, dict) or game.get("statusGroup") != 3:
                continue
            home = game.get("homeCompetitor") or {}
            away = game.get("awayCompetitor") or {}
            provider_id = str(game.get("id") or "")
            if (
                not _FIXTURE_ID.fullmatch(provider_id)
                or not home.get("name")
                or not away.get("name")
            ):
                continue
            fixtures.append(
                ProviderFixture(
                    provider_fixture_id=provider_id,
                    competition=game.get("competitionDisplayName")
                    or game.get("stageName")
                    or "365Scores",
                    home_name=str(home["name"]),
                    away_name=str(away["name"]),
                    home_logo_url=home.get("logo"),
                    away_logo_url=away.get("logo"),
                    scheduled_at=_parse_datetime(game.get("startTime")),
                    status=str(game.get("statusText") or "live"),
                )
            )
        return fixtures

    def parse_snapshot(self, payload: dict[str, Any]) -> ProviderSnapshot:
        if not isinstance(payload, dict):
            raise ProviderUnavailable("provider unavailable")
        game = payload.get("game")
        if not isinstance(game, dict):
            raise ProviderUnavailable("provider unavailable")
        home = game.get("homeCompetitor") or {}
        away = game.get("awayCompetitor") or {}
        provider_id = str(game.get("id") or "")
        if (
            not _FIXTURE_ID.fullmatch(provider_id)
            or not home.get("name")
            or not away.get("name")
            or not isinstance(home.get("id"), int)
            or not isinstance(away.get("id"), int)
        ):
            raise ProviderUnavailable("provider unavailable")
        score_home, score_away = parse_score(game)
        if score_home is None or score_away is None:
            raise ProviderUnavailable("provider unavailable")

        minute_value = parse_minute(game)
        status = str(game.get("statusText") or "")
        if minute_value is None and status.lower() in {
            "descanso",
            "half time",
            "ht",
            "medio tiempo",
        }:
            minute_value = 45.0
        minute = int(minute_value) if minute_value is not None else None

        normalized = parse_statistics(payload, home["id"], away["id"])
        stats = ProviderStats(
            **{name: HomeAwayStat(**values) for name, values in normalized.items()}
        )
        partial = any(
            pair.home is None or pair.away is None
            for pair in (
                stats.possession,
                stats.shots,
                stats.shots_on_target,
                stats.corners,
                stats.yellow_cards,
                stats.red_cards,
                stats.fouls,
                stats.offsides,
                stats.expected_goals,
            )
        )
        return ProviderSnapshot(
            provider_fixture_id=provider_id,
            home_name=str(home["name"]),
            away_name=str(away["name"]),
            minute=minute,
            score_home=score_home,
            score_away=score_away,
            stats=stats,
            provider_observed_at=self._clock(),
            quality=DataStatus.DEGRADED if partial else DataStatus.FRESH,
            sanitized_provider_data={"status": status},
        )


class ProviderClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
        adapter: ProviderAdapter | None = None,
    ) -> None:
        if client is not None and transport is not None:
            raise ValueError("inject either client or transport, not both")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        self._owns_client = client is None
        if client is not None:
            if str(client.base_url) != PROVIDER_BASE_URL:
                raise ValueError("injected client must use the fixed provider base URL")
            self._client = client
        else:
            self._client = httpx.AsyncClient(
                base_url=PROVIDER_BASE_URL,
                headers=_HEADERS,
                timeout=httpx.Timeout(
                    TOTAL_TIMEOUT_SECONDS,
                    connect=CONNECT_TIMEOUT_SECONDS,
                ),
                limits=httpx.Limits(
                    max_connections=MAX_CONNECTIONS,
                    max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
                ),
                follow_redirects=False,
                transport=transport,
            )
        self._max_response_bytes = max_response_bytes
        self._sleep = sleep
        self._jitter = jitter
        self._adapter = adapter or ProviderAdapter()

    async def __aenter__(self) -> ProviderClient:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_live(self) -> list[ProviderFixture]:
        today = datetime.now().strftime("%d/%m/%Y")
        payload = await self._request_json(
            LIVE_LIST_PATH,
            {"sports": "1", "startDate": today, "endDate": today},
        )
        try:
            return self._adapter.parse_live_list(payload)
        except ProviderUnavailable:
            raise
        except Exception:
            raise ProviderUnavailable("provider unavailable") from None

    async def get_snapshot(self, provider_fixture_id: str) -> ProviderSnapshot:
        self._validate_fixture_id(provider_fixture_id)
        game_payload = await self._request_json(
            GAME_PATH,
            {"gameId": provider_fixture_id, "topBookmaker": "14"},
        )
        stats_payload = await self._request_json(
            GAME_STATS_PATH,
            {"games": provider_fixture_id},
        )
        combined = {
            "game": game_payload.get("game"),
            "statistics": stats_payload.get("statistics"),
        }
        try:
            return self._adapter.parse_snapshot(combined)
        except ProviderUnavailable:
            raise
        except Exception:
            raise ProviderUnavailable("provider unavailable") from None

    @staticmethod
    def _validate_fixture_id(provider_fixture_id: str) -> None:
        if not isinstance(provider_fixture_id, str) or not _FIXTURE_ID.fullmatch(
            provider_fixture_id
        ):
            raise ValueError("provider fixture id must contain 1 to 20 ASCII digits")

    async def _request_json(
        self, path: str, params: dict[str, str]
    ) -> dict[str, Any]:
        if path not in _ALLOWED_PATHS:
            raise ProviderUnavailable("provider unavailable")
        request_params = {**_COMMON_PARAMS, **params}
        for attempt in range(MAX_ATTEMPTS):
            retry = False
            try:
                async with self._client.stream(
                    "GET", path, params=request_params
                ) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        retry = attempt + 1 < MAX_ATTEMPTS
                        if not retry:
                            raise ProviderUnavailable("provider unavailable")
                    elif not 200 <= response.status_code < 300:
                        raise ProviderUnavailable("provider unavailable")
                    else:
                        return await self._read_json(response)
            except httpx.TimeoutException:
                retry = attempt + 1 < MAX_ATTEMPTS
                if not retry:
                    raise ProviderUnavailable("provider unavailable") from None
            except ProviderUnavailable:
                raise
            except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
                raise ProviderUnavailable("provider unavailable") from None

            if retry:
                jitter = min(max(float(self._jitter()), 0.0), 1.0)
                delay = min(0.25 * (2**attempt) + 0.1 * jitter, 1.0)
                await self._sleep(delay)
        raise ProviderUnavailable("provider unavailable")

    async def _read_json(self, response: httpx.Response) -> dict[str, Any]:
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self._max_response_bytes:
                    raise ProviderUnavailable("provider unavailable")
            except ValueError:
                raise ProviderUnavailable("provider unavailable") from None

        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._max_response_bytes:
                raise ProviderUnavailable("provider unavailable")
            chunks.append(chunk)
        payload = json.loads(b"".join(chunks).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ProviderUnavailable("provider unavailable")
        return payload
