from unittest.mock import AsyncMock
import asyncio
from types import SimpleNamespace

from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.methods import GetMe
import pytest

import bot
from utils.bot_health import BotHealth


async def test_startup_recovers_from_transport_failure(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(bot.asyncio, "sleep", sleep)
    operation = AsyncMock(side_effect=[TelegramNetworkError(method=GetMe(), message="synthetic"), True])
    assert await bot.retry_startup_network(operation) is True
    assert operation.await_count == 2
    assert sleep.await_count == 1


async def test_startup_does_not_retry_invalid_credentials(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(bot.asyncio, "sleep", sleep)
    operation = AsyncMock(side_effect=TelegramUnauthorizedError(method=GetMe(), message="synthetic"))
    with pytest.raises(TelegramUnauthorizedError):
        await bot.retry_startup_network(operation)
    assert operation.await_count == 1
    sleep.assert_not_awaited()


async def test_startup_retries_are_bounded(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(bot.asyncio, "sleep", sleep)
    operation = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(TimeoutError):
        await bot.retry_startup_network(operation, attempts=3)
    assert operation.await_count == 3
    assert sleep.await_count == 2


async def test_polling_starts_while_optional_configuration_is_blocked_and_shutdown_cleans_up(tmp_path,monkeypatch):
    fake=SimpleNamespace(me=AsyncMock(return_value=True),delete_webhook=AsyncMock(),session=SimpleNamespace(close=AsyncMock()))
    configured=asyncio.Event()
    async def blocked(*args):
        configured.set()
        await asyncio.Event().wait()
    async def poll(*args,**kwargs):
        await asyncio.wait_for(configured.wait(),1)
    monkeypatch.setattr(bot,"init_db",AsyncMock())
    monkeypatch.setattr(bot,"configure_menu",blocked)
    monkeypatch.setattr(bot,"observed_notifier",blocked)
    dp=SimpleNamespace(start_polling=AsyncMock(side_effect=poll))
    health=BotHealth(tmp_path/"health.json")
    await bot.run_bot(fake,dp,health)
    fake.me.assert_awaited_once()
    fake.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
    dp.start_polling.assert_awaited_once_with(fake,polling_timeout=25,close_bot_session=False)
    fake.session.close.assert_awaited_once()
    assert health.data["phase"]=="stopped"


async def test_invalid_credentials_fail_before_polling_and_close_session(tmp_path,monkeypatch):
    fake=SimpleNamespace(me=AsyncMock(side_effect=TelegramUnauthorizedError(method=GetMe(),message="synthetic")),
        delete_webhook=AsyncMock(),session=SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(bot,"init_db",AsyncMock())
    dp=SimpleNamespace(start_polling=AsyncMock())
    with pytest.raises(TelegramUnauthorizedError):
        await bot.run_bot(fake,dp,BotHealth(tmp_path/"health.json"))
    fake.delete_webhook.assert_not_awaited();dp.start_polling.assert_not_awaited()
    fake.session.close.assert_awaited_once()


async def test_optional_menu_configuration_recovers_after_a_network_failure(tmp_path,monkeypatch):
    monkeypatch.setattr(bot,"WEBAPP_URL",None)
    monkeypatch.setattr(bot.asyncio,"sleep",AsyncMock())
    operation=AsyncMock(side_effect=[TelegramNetworkError(method=GetMe(),message="synthetic"),True])
    monkeypatch.setattr(bot,"retry_startup_network",operation)
    health=BotHealth(tmp_path/"health.json")
    await bot.configure_menu(SimpleNamespace(set_my_commands=lambda value:None),health)
    assert operation.await_count==2 and not health.data["setup_errors"] and not health.data["setup_pending"]
