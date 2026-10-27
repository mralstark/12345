from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.methods import GetMe
import pytest

import bot


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
