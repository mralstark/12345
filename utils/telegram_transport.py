"""Refresh failed DNS cache entries without retrying side-effecting requests."""
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramNetworkError
from aiohttp import TCPConnector
import logging


class DNSRecoveryMiddleware(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        try:
            return await make_request(bot, method)
        except TelegramNetworkError:
            try:
                session = bot.session
                if isinstance(session, AiohttpSession) and not session.proxy:
                    # Do not close the shared session: other sends may be in flight.
                    client = await session.create_session()
                    if isinstance(client.connector, TCPConnector):
                        client.connector.clear_dns_cache()
            except Exception as exc:
                logging.getLogger(__name__).warning("Telegram DNS cache recovery failed: %s", type(exc).__name__)
            # The polling loop / existing notification policy decides on retries.
            # Never replay sendMessage here: a timeout may follow successful delivery.
            raise


def create_bot_session(proxy=None):
    session = AiohttpSession(proxy=proxy)
    session.middleware.register(DNSRecoveryMiddleware())
    return session
