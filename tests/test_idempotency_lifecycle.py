import asyncio

import pytest
from starlette.requests import Request
from starlette.responses import StreamingResponse

from api.idempotency import IdempotencyStore


def request(key="article-upload-lifecycle-test"):
    return Request({"type": "http", "method": "POST", "path": "/api/news/articles/1/photos",
                    "headers": [(b"idempotency-key", key.encode()), (b"authorization", b"tma test")],
                    "query_string": b"", "server": ("test", 80), "scheme": "http"})


@pytest.mark.parametrize("temporary_status", [429, 500, 502, 503, 504])
async def test_transient_result_does_not_poison_same_key_retry(temporary_status):
    store = IdempotencyStore()
    calls = 0
    async def handle(_):
        nonlocal calls
        calls += 1
        return StreamingResponse(iter([b"result"]), status_code=temporary_status if calls == 1 else 200)
    assert (await store.execute(request(), handle)).status_code == temporary_status
    assert (await store.execute(request(), handle)).status_code == 200
    assert calls == 2
    assert (await store.execute(request(), handle)).status_code == 200
    assert calls == 2


async def test_live_request_cannot_expire_or_be_evicted_by_new_keys():
    store = IdempotencyStore(ttl_seconds=0, max_entries=1)
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def handle(_):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return StreamingResponse(iter([b"ok"]))
    first = asyncio.create_task(store.execute(request(), handle))
    await started.wait()
    duplicate = asyncio.create_task(store.execute(request(), handle))
    await asyncio.sleep(0)
    # A different key cannot remove the live operation to make room.
    blocked = await store.execute(request("a-different-upload-lifecycle-key"), handle)
    assert blocked.status_code == 503
    assert calls == 1 and not duplicate.done()
    release.set()
    responses = await asyncio.gather(first, duplicate)
    assert [r.status_code for r in responses] == [200, 200]
    assert calls == 1
