import asyncio
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

import football_live.provider as provider_module
from football_live.provider import (
    ProviderAdapter,
    ProviderClient,
    ProviderUnavailable,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "365scores"
OBSERVED_AT = datetime(2026, 9, 24, 20, 3, 44, tzinfo=timezone.utc)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


async def no_sleep(_delay: float) -> None:
    return None


class VirtualClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class AdvancingJsonStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes, clock: VirtualClock, elapsed: float) -> None:
        self._body = body
        self._clock = clock
        self._elapsed = elapsed
        self.completed = False

    async def __aiter__(self):
        self._clock.advance(self._elapsed)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.completed = True
        yield self._body


def install_virtual_clock(monkeypatch) -> tuple[VirtualClock, float]:
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    clock = VirtualClock(started_at)
    monkeypatch.setattr(loop, "time", lambda: clock.now)
    return clock, started_at


@pytest.mark.asyncio
async def test_total_deadline_includes_retry_backoff(monkeypatch):
    clock, started_at = install_virtual_clock(monkeypatch)
    attempts = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    async def advance_backoff(delay: float) -> None:
        delays.append(delay)
        clock.advance(6.1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async with ProviderClient(
        transport=httpx.MockTransport(handler),
        sleep=advance_backoff,
        jitter=lambda: 0.0,
    ) as client:
        with pytest.raises(ProviderUnavailable, match="^provider unavailable$"):
            await client.list_live()

    assert attempts == 2
    assert len(delays) == 2
    assert clock.now - started_at == pytest.approx(12.2)


@pytest.mark.asyncio
async def test_snapshot_deadline_spans_both_slow_streaming_responses(monkeypatch):
    clock, started_at = install_virtual_clock(monkeypatch)
    partial = load_fixture("game_partial.json")
    requests: list[httpx.Request] = []
    streams: list[AdvancingJsonStream] = []

    def response(payload: dict, elapsed: float) -> httpx.Response:
        stream = AdvancingJsonStream(json.dumps(payload).encode(), clock, elapsed)
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/web/game/":
            return response({"game": partial["game"]}, 7.0)
        if request.url.path == "/web/game/stats/":
            return response({"statistics": partial["statistics"]}, 6.0)
        return httpx.Response(404)

    async with ProviderClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderUnavailable, match="^provider unavailable$"):
            await client.get_snapshot("123456789")

    assert [request.url.path for request in requests] == [
        "/web/game/",
        "/web/game/stats/",
    ]
    assert clock.now - started_at == pytest.approx(13.0)
    assert streams[0].completed is True
    assert streams[1].completed is False


def test_client_constructs_fixed_outbound_transport(monkeypatch):
    captured: dict = {}

    class StubAsyncClient:
        pass

    def capture_async_client(**kwargs):
        captured.update(kwargs)
        return StubAsyncClient()

    monkeypatch.setattr(provider_module.httpx, "AsyncClient", capture_async_client)

    ProviderClient()

    timeout = captured["timeout"]
    limits = captured["limits"]
    assert "client" not in inspect.signature(ProviderClient).parameters
    assert captured["base_url"] == "https://webws.365scores.com/web/"
    assert captured["follow_redirects"] is False
    assert timeout.connect == 5.0
    assert limits.max_connections == 10
    assert limits.max_keepalive_connections == 5


def test_partial_snapshot_preserves_missing_stats_and_real_zero():
    snapshot = ProviderAdapter(clock=lambda: OBSERVED_AT).parse_snapshot(
        load_fixture("game_partial.json")
    )

    assert snapshot.provider_fixture_id == "123456789"
    assert snapshot.home_name == "Club Norte"
    assert snapshot.away_name == "Club Sur"
    assert snapshot.score_home == 1
    assert snapshot.score_away == 0
    assert snapshot.stats.possession.home == 55
    assert snapshot.stats.possession.away is None
    assert snapshot.stats.shots_on_target.home == 4
    assert snapshot.stats.shots_on_target.away is None
    assert snapshot.stats.expected_goals.away == 0
    assert snapshot.quality == "degraded"


def test_live_list_uses_canonical_fields_and_internal_live_status():
    payload = load_fixture("live_list.json")
    payload["games"][0]["homeCompetitor"].update({"id": 11, "imageVersion": 7})
    payload["games"][0]["awayCompetitor"].update({"id": 22, "imageVersion": 3})

    fixtures = ProviderAdapter().parse_live_list(payload)

    assert len(fixtures) == 1
    assert fixtures[0].provider_fixture_id == "123456789"
    assert fixtures[0].home_name == "Club Norte"
    assert fixtures[0].away_name == "Club Sur"
    assert fixtures[0].competition == "Liga de prueba"
    assert fixtures[0].status == "live"
    assert fixtures[0].home_logo_url == (
        "https://imagecache.365scores.com/image/upload/"
        "f_png,w_160,h_160,c_limit,q_auto:eco,dpr_2,d_Competitors:default1.png/"
        "v7/Competitors/11"
    )
    assert fixtures[0].away_logo_url.endswith("/v3/Competitors/22")


@pytest.mark.asyncio
async def test_client_uses_only_the_three_fixed_365scores_destinations():
    requests: list[httpx.Request] = []
    live = load_fixture("live_list.json")
    partial = load_fixture("game_partial.json")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/web/games/allscores/":
            return httpx.Response(200, json=live)
        if request.url.path == "/web/game/":
            return httpx.Response(200, json={"game": partial["game"]})
        if request.url.path == "/web/game/stats/":
            return httpx.Response(200, json={"statistics": partial["statistics"]})
        return httpx.Response(404)

    async with ProviderClient(
        transport=httpx.MockTransport(handler), sleep=no_sleep, jitter=lambda: 0.0
    ) as client:
        await client.list_live()
        await client.get_snapshot("123456789")

    assert [(request.url.scheme, request.url.host, request.url.path) for request in requests] == [
        ("https", "webws.365scores.com", "/web/games/allscores/"),
        ("https", "webws.365scores.com", "/web/game/"),
        ("https", "webws.365scores.com", "/web/game/stats/"),
    ]
    assert requests[1].url.params["gameId"] == "123456789"
    assert requests[2].url.params["games"] == "123456789"
    assert "url" not in inspect.signature(ProviderClient.get_snapshot).parameters
    assert "url" not in inspect.signature(ProviderClient.list_live).parameters


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_id",
    ["", "https://attacker.invalid/", "../42", "１２３", "1" * 21],
)
async def test_provider_id_is_rejected_before_network(invalid_id: str):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with ProviderClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="provider fixture id"):
            await client.get_snapshot(invalid_id)

    assert calls == 0


@pytest.mark.asyncio
async def test_snapshot_rejects_mismatched_game_id_before_stats_request():
    partial = load_fixture("game_partial.json")
    mismatched_game = {**partial["game"], "id": 987654321}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/web/game/":
            return httpx.Response(200, json={"game": mismatched_game})
        return httpx.Response(200, json={"statistics": partial["statistics"]})

    async with ProviderClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderUnavailable, match="^provider unavailable$"):
            await client.get_snapshot("123456789")

    assert [request.url.path for request in requests] == ["/web/game/"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "429", "500"])
async def test_only_retryable_categories_can_recover_on_third_attempt(failure: str):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            if failure == "timeout":
                raise httpx.ReadTimeout("provider stalled", request=request)
            return httpx.Response(int(failure))
        return httpx.Response(200, json=load_fixture("live_list.json"))

    async with ProviderClient(
        transport=httpx.MockTransport(handler), sleep=no_sleep, jitter=lambda: 0.0
    ) as client:
        fixtures = await client.list_live()

    assert attempts == 3
    assert fixtures[0].provider_fixture_id == "123456789"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["400", "transport"])
async def test_non_retryable_failures_stop_after_one_attempt(failure: str):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if failure == "transport":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(400)

    async with ProviderClient(
        transport=httpx.MockTransport(handler), sleep=no_sleep, jitter=lambda: 0.0
    ) as client:
        with pytest.raises(ProviderUnavailable, match="^provider unavailable$"):
            await client.list_live()

    assert attempts == 1


@pytest.mark.asyncio
async def test_retryable_failure_is_capped_at_three_attempts():
    attempts = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    async with ProviderClient(
        transport=httpx.MockTransport(handler),
        sleep=record_sleep,
        jitter=lambda: 0.0,
    ) as client:
        with pytest.raises(ProviderUnavailable, match="^provider unavailable$"):
            await client.list_live()

    assert attempts == 3
    assert len(delays) == 2
    assert all(0 <= delay <= 1 for delay in delays)
    assert sum(delays) < 12


@pytest.mark.asyncio
async def test_response_size_guard_rejects_oversized_body_without_retry():
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, content=b"x" * 65)

    async with ProviderClient(
        transport=httpx.MockTransport(handler), max_response_bytes=64
    ) as client:
        with pytest.raises(ProviderUnavailable, match="^provider unavailable$"):
            await client.list_live()

    assert attempts == 1


@pytest.mark.asyncio
async def test_timeout_error_does_not_expose_external_details():
    secret_detail = "https://webws.365scores.com/web/game/?tracking=private-id"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(secret_detail, request=request)

    async with ProviderClient(
        transport=httpx.MockTransport(handler), sleep=no_sleep, jitter=lambda: 0.0
    ) as client:
        with pytest.raises(ProviderUnavailable) as exc_info:
            await client.list_live()

    assert str(exc_info.value) == "provider unavailable"
    assert secret_detail not in repr(exc_info.value)
