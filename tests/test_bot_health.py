import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.methods import GetMe, GetUpdates, SendMessage

from utils.bot_health import (
    BotHealth, TelegramHealthMiddleware, UpdateHealthMiddleware,
    read_health, readiness_issues,
)


def healthy(health):
    health.data.update(phase="polling", last_poll_success_at=1000, updated_at=1000,
                       setup_pending=False, notifier_running=True)
    return health.data


async def test_empty_poll_response_proves_readiness_but_get_me_does_not(tmp_path):
    health = BotHealth(tmp_path / "health.json")
    middleware = TelegramHealthMiddleware(health)
    await middleware(AsyncMock(return_value=True), None, GetMe())
    assert health.data["last_poll_success_at"] is None
    assert "bot-polling-not-ready" in readiness_issues(health.data)
    await middleware(AsyncMock(return_value=[]), None, GetUpdates(timeout=25))
    assert health.data["poll_successes"] == 1
    assert health.data["updates_received"] == 0
    health.data.update(setup_pending=False, notifier_running=True)
    health.write()
    assert readiness_issues(health.data) == []


async def test_poll_failures_do_not_falsify_last_success_or_consume_updates(tmp_path):
    health = BotHealth(tmp_path / "health.json")
    healthy(health)
    method = GetUpdates(timeout=25, offset=700)
    error = TelegramNetworkError(method=method, message="synthetic private diagnostic")
    request = AsyncMock(side_effect=error)
    with pytest.raises(TelegramNetworkError):
        await TelegramHealthMiddleware(health)(request, None, method)
    assert method.offset == 700 and request.await_count == 1
    assert health.data["last_poll_success_at"] == 1000
    assert health.data["poll_failures"] == 1 and health.data["poll_error_kind"] == "network"
    health.data["updated_at"] = 1200
    assert readiness_issues(health.data, now=1200) == ["bot-polling-stale"]
    assert "private diagnostic" not in json.dumps(health.data)


async def test_roundtrip_requires_incoming_handler_and_successful_send(tmp_path):
    health = BotHealth(tmp_path / "health.json")
    requests = TelegramHealthMiddleware(health)
    updates = UpdateHealthMiddleware(health)
    message = SendMessage(chat_id=999999, text="SYNTHETIC-PRIVATE-TEXT")
    async def reply(event, data):
        await requests(AsyncMock(return_value=True), None, message)
    await requests(AsyncMock(return_value=True), None, message)
    assert health.data["last_roundtrip_at"] is None  # Background send alone is insufficient.
    await updates(reply, object(), {})
    assert health.data["last_roundtrip_at"] is not None
    health.data["last_roundtrip_at"] = None
    async def fail(event, data):
        raise RuntimeError("synthetic")
    with pytest.raises(RuntimeError):
        await updates(fail, object(), {})
    assert health.data["last_roundtrip_at"] is None and health.data["handler_failures"] == 1
    health.write()
    raw = health.path.read_text()
    assert "999999" not in raw and "SYNTHETIC-PRIVATE-TEXT" not in raw


async def test_concurrent_update_context_does_not_attribute_another_tasks_send(tmp_path):
    health = BotHealth(tmp_path / "health.json")
    requests, updates = TelegramHealthMiddleware(health), UpdateHealthMiddleware(health)
    sent = asyncio.Event()
    finish = asyncio.Event()
    async def reply(event, data):
        await requests(AsyncMock(return_value=True), None, SendMessage(chat_id=1,text="synthetic"))
        sent.set()
        await finish.wait()
    async def silent(event, data):
        await sent.wait()
    task = asyncio.create_task(updates(reply, object(), {}))
    await updates(silent, object(), {})
    assert health.data["last_roundtrip_at"] is None
    finish.set(); await task
    assert health.data["last_roundtrip_at"] is not None


def test_state_rejects_previous_process_wrong_invocation_oversize_and_public_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "a" * 32)
    health = BotHealth(tmp_path / "health.json"); health.write()
    assert read_health(health.path,os.getpid(),"a"*32)["version"] == 1
    for pid,invocation in [(os.getpid()+1,"a"*32),(os.getpid(),"b"*32),(os.getpid(),"")]:
        with pytest.raises(ValueError):read_health(health.path,pid,invocation)
    health.path.write_text("x"*16385)
    with pytest.raises(ValueError):read_health(health.path,os.getpid(),"a"*32)
    if os.name == "posix":
        health.write(); health.path.chmod(0o644)
        with pytest.raises(ValueError):read_health(health.path,os.getpid(),"a"*32)


def test_monitor_detects_stale_loop_startup_stopped_notifier_and_future_clock(tmp_path):
    state = healthy(BotHealth(tmp_path / "health.json"))
    assert readiness_issues(state,now=1000) == []
    state["updated_at"] = 900
    assert "bot-heartbeat-stale" in readiness_issues(state,now=1000)
    state["updated_at"] = 1100
    assert "bot-heartbeat-stale" in readiness_issues(state,now=1000)
    state.update(updated_at=1000,phase="starting",last_poll_success_at=None)
    assert "bot-polling-not-ready" in readiness_issues(state,now=1000)
    state.update(phase="polling",last_poll_success_at=1000,notifier_running=False,poll_error_kind="conflict")
    assert "bot-notifier-stopped" in readiness_issues(state,now=1000)
    assert "bot-polling-conflict" in readiness_issues(state,now=1000)


@pytest.mark.skipif(os.name!="posix",reason="POSIX private state file protections")
def test_monitor_rejects_fifo_and_symlink_without_blocking(tmp_path,monkeypatch):
    monkeypatch.setenv("INVOCATION_ID","a"*32)
    health=BotHealth(tmp_path/"health.json");health.write()
    link=tmp_path/"link";link.symlink_to(health.path)
    with pytest.raises(OSError):read_health(link,os.getpid(),"a"*32)
    fifo=tmp_path/"fifo";os.mkfifo(fifo,0o600)
    with pytest.raises(ValueError):read_health(fifo,os.getpid(),"a"*32)


async def test_transport_refreshes_dns_once_without_replaying_or_closing_shared_requests(monkeypatch):
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiohttp import TCPConnector
    from utils.telegram_transport import DNSRecoveryMiddleware
    session = AiohttpSession()
    connector = MagicMock(spec=TCPConnector)
    monkeypatch.setattr(session,"create_session",AsyncMock(return_value=SimpleNamespace(connector=connector)))
    monkeypatch.setattr(session,"close",AsyncMock())
    method = SendMessage(chat_id=1,text="synthetic")
    error = TelegramNetworkError(method=method,message="synthetic")
    request = AsyncMock(side_effect=error)
    with pytest.raises(TelegramNetworkError) as exc:
        await DNSRecoveryMiddleware()(request,SimpleNamespace(session=session),method)
    assert exc.value is error and request.await_count == 1
    connector.clear_dns_cache.assert_called_once()
    session.close.assert_not_awaited()
    request = AsyncMock(side_effect=TelegramUnauthorizedError(method=GetMe(),message="synthetic"))
    with pytest.raises(TelegramUnauthorizedError):
        await DNSRecoveryMiddleware()(request,SimpleNamespace(session=session),GetMe())
    assert connector.clear_dns_cache.call_count == 1


async def test_factory_keeps_authenticated_tls_and_does_not_add_a_proxy():
    import ssl
    from utils.telegram_transport import create_bot_session
    session=create_bot_session()
    context=session._connector_init["ssl"]
    assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname
    assert context.minimum_version>=ssl.TLSVersion.TLSv1_2 and session.proxy is None
    await session.close()
