import pytest

from app.services.chat_flood import ChatFloodLimiter


class _FakeRedis:
    """Emulates the INCR/PTTL/EXPIRE Lua script; ``expire_all`` simulates TTL expiry."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}  # seconds; absent = no TTL

    async def eval(self, script, numkeys, key, ttl):
        assert "INCR" in script and "EXPIRE" in script and numkeys == 1
        self.counts[key] = self.counts.get(key, 0) + 1
        if self.counts[key] == 1 or key not in self.ttls:
            self.ttls[key] = ttl
        return self.counts[key]

    def expire_all(self) -> None:
        self.counts.clear()
        self.ttls.clear()


class _BrokenRedis:
    async def eval(self, *a, **k):
        raise ConnectionError("down")


async def _burst(limiter, room, user, n, redis=None):
    if (room, user) not in limiter._stamps:
        limiter.acquire(room, user)
    return [await limiter.is_limited(room, user, redis) for _ in range(n)]


@pytest.mark.asyncio
async def test_same_user_connections_share_budget():
    lim = ChatFloodLimiter()
    lim.acquire("r", "u")
    lim.acquire("r", "u")
    res = await _burst(lim, "r", "u", 10)
    assert res == [False] * 9 + [True]


@pytest.mark.asyncio
async def test_other_users_and_rooms_unaffected():
    lim = ChatFloodLimiter()
    for room, user in (("r", "u"), ("r", "v"), ("s", "u")):
        lim.acquire(room, user)
    assert (await _burst(lim, "r", "u", 10))[-1] is True
    assert await lim.is_limited("r", "v") is False
    assert await lim.is_limited("s", "u") is False


@pytest.mark.asyncio
async def test_window_expires(monkeypatch):
    lim = ChatFloodLimiter(max_messages=3, window=10)
    now = [100.0]

    class _T:
        monotonic = staticmethod(lambda: now[0])

    monkeypatch.setattr("app.services.chat_flood.time", _T)
    lim.acquire("r", "u")
    assert (await _burst(lim, "r", "u", 3))[-1] is True
    now[0] += 11
    assert await lim.is_limited("r", "u") is False


def test_state_cleaned_up_after_last_release():
    lim = ChatFloodLimiter()
    lim.acquire("r", "u")
    lim.acquire("r", "u")
    lim.release("r", "u")
    assert ("r", "u") in lim._stamps
    lim.release("r", "u")
    assert not lim._stamps and not lim._refs
    lim.release("r", "u")  # idempotent
    assert not lim._refs


@pytest.mark.asyncio
async def test_redis_counter_shared_across_instances():
    redis = _FakeRedis()
    a, b = ChatFloodLimiter(), ChatFloodLimiter()
    res = []
    for i in range(10):
        res.append(await (a if i % 2 else b).is_limited("r", "u", redis))
    assert res == [False] * 9 + [True]
    assert redis.ttls["ws:flood:r:u"] == 10


@pytest.mark.asyncio
async def test_redis_failure_falls_back_to_local():
    lim = ChatFloodLimiter()
    res = await _burst(lim, "r", "u", 10, _BrokenRedis())
    assert res == [False] * 9 + [True]


@pytest.mark.asyncio
async def test_redis_failure_opens_circuit_breaker():
    lim = ChatFloodLimiter()
    calls = 0

    class _Counting:
        async def eval(self, *a, **k):
            nonlocal calls
            calls += 1
            raise ConnectionError("down")

    await _burst(lim, "r", "u", 5, _Counting())
    assert calls == 1


@pytest.mark.asyncio
async def test_redis_key_expiring_between_calls_starts_fresh_window():
    redis = _FakeRedis()
    lim = ChatFloodLimiter()
    assert (await _burst(lim, "r", "u", 9, redis))[-1] is False
    redis.expire_all()  # TTL elapsed between two frames
    lim._stamps[("r", "u")].clear()
    assert await lim.is_limited("r", "u", redis) is False
    assert redis.ttls["ws:flood:r:u"] == 10


@pytest.mark.asyncio
async def test_redis_ttl_less_key_is_healed():
    redis = _FakeRedis()
    redis.counts["ws:flood:r:u"] = 20  # stale key that lost its TTL
    lim = ChatFloodLimiter()
    lim.acquire("r", "u")
    assert await lim.is_limited("r", "u", redis) is True
    assert redis.ttls["ws:flood:r:u"] == 10


@pytest.mark.asyncio
async def test_is_limited_without_acquire_leaves_no_state():
    lim = ChatFloodLimiter()
    assert await lim.is_limited("r", "u") is False
    assert not lim._stamps and not lim._refs
