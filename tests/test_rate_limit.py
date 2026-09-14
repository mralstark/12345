from api.rate_limit import SlidingWindowLimiter


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
