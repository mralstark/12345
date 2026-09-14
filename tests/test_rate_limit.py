from api.rate_limit import SlidingWindowLimiter
from utils.bot_rate_limit import BotRateLimitMiddleware


def test_sliding_window_limiter_rejects_request_after_budget():
    limiter = SlidingWindowLimiter()
    assert limiter.check("write", 1001, limit=2, window_seconds=60) is None
    assert limiter.check("write", 1001, limit=2, window_seconds=60) is None
    assert limiter.check("write", 1001, limit=2, window_seconds=60) is not None


def test_sliding_window_budgets_are_separate_by_user_and_scope():
    limiter = SlidingWindowLimiter()
    assert limiter.check("upload", 1001, limit=1, window_seconds=60) is None
    assert limiter.check("read", 1001, limit=1, window_seconds=60) is None
    assert limiter.check("upload", 1002, limit=1, window_seconds=60) is None


def test_sliding_window_limiter_has_hard_key_bound():
    limiter = SlidingWindowLimiter(max_keys=2)
    for subject in range(10):
        assert limiter.check("read", subject, limit=2, window_seconds=60) is None

    assert len(limiter._events) == 2


async def test_bot_rate_limit_stops_spam_before_handler():
    middleware = BotRateLimitMiddleware()
    event = type("Event", (), {"from_user": type("Actor", (), {"id": 123})()})()
    calls = 0

    async def handler(_event, _data):
        nonlocal calls
        calls += 1
        return "handled"

    for _ in range(60):
        assert await middleware(handler, event, {}) == "handled"
    assert await middleware(handler, event, {}) is None
    assert calls == 60
