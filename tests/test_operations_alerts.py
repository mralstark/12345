from unittest.mock import AsyncMock
import pytest

from services.operations_alerts import alert_health


async def test_alerts_are_opt_in_not_sent_for_startup_and_rate_limited(tmp_path):
    sender = AsyncMock(return_value=True)
    state={"issues":["bot-polling-stale"],"consecutive_failures":2}
    assert not await alert_health(tmp_path,state,"",now=10000,sender=sender)
    assert not await alert_health(tmp_path,state,"@someone",now=10000,sender=sender)
    sender.assert_not_awaited()
    assert not await alert_health(tmp_path,dict(state,consecutive_failures=1),"123",now=10000,sender=sender)
    assert await alert_health(tmp_path,state,"123",now=10000,sender=sender)
    assert not await alert_health(tmp_path,state,"123",now=10100,sender=sender)
    assert await alert_health(tmp_path,state,"123",now=11801,sender=sender)
    assert sender.await_count == 2
    assert await alert_health(tmp_path,{"issues":[],"consecutive_failures":0},"123",now=11900,sender=sender)
    assert not await alert_health(tmp_path,{"issues":[],"consecutive_failures":0},"123",now=12000,sender=sender)
    assert "восстановлена" in sender.await_args.args[1]


async def test_failed_outage_delivery_is_remembered_and_reported_after_recovery(tmp_path):
    sender=AsyncMock(side_effect=[TimeoutError(),True])
    state={"issues":["bot-polling-stale"],"consecutive_failures":3}
    assert not await alert_health(tmp_path,state,"123",now=10000,sender=sender)
    assert not await alert_health(tmp_path,state,"123",now=10100,sender=sender)
    assert sender.await_count==1
    assert await alert_health(tmp_path,{"issues":[],"consecutive_failures":0},"123",now=10200,sender=sender)
    assert "bot-polling-stale" in sender.await_args.args[1]
    assert not await alert_health(tmp_path,{"issues":[],"consecutive_failures":0},"123",now=10300,sender=sender)
