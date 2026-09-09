"""Отправка уведомлений в Telegram (ТЗ §14).

Уведомления шлёт и бот, и API (когда письмо или задача создаются из Mini App),
поэтому здесь общий ленивый экземпляр Bot: свой на процесс, создаётся при первой
отправке. В процессе бота aiogram работает со своим Bot — это нормально, оба
пользуются одним и тем же токеном и разными HTTP-сессиями.
"""

import asyncio
import logging

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramNetworkError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy.ext.asyncio import AsyncSession

from config import BOT_PROXY_URL, BOT_TOKEN, WEBAPP_URL
from database.models import ROLE_LABELS, User
from utils.parser import first_and_patronymic

logger = logging.getLogger(__name__)

_bot: Bot | None = None

# Ретраи только на сетевые сбои (например, разовый DNS-таймаут на сервере) —
# TelegramForbiddenError/BadRequest и т.п. повтором не лечатся.
_NETWORK_RETRIES = 2
_NETWORK_RETRY_DELAY = 2.0


def get_notifier_bot() -> Bot | None:
    global _bot
    if not BOT_TOKEN:
        return None
    if _bot is None:
        session = AiohttpSession(proxy=BOT_PROXY_URL) if BOT_PROXY_URL else None
        _bot = Bot(token=BOT_TOKEN, session=session)
    return _bot


async def close_notifier_bot() -> None:
    global _bot
    if _bot is not None:
        await _bot.session.close()
        _bot = None


async def notify_telegram(
    telegram_id: int | None,
    text: str,
    bot: Bot | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    retries: int = _NETWORK_RETRIES,
    retry_delay: float = _NETWORK_RETRY_DELAY,
    fallback_text: str | None = None,
) -> Message | None:
    """Пытается доставить уведомление. Молча переживает блокировку бота
    пользователем: недоставленное уведомление не должно ронять операцию,
    ради которой оно отправлялось.

    fallback_text — запасной вариант того же сообщения, попроще. Нужен там, где
    в тексте есть то, что Telegram может не принять именно у этого бота:
    кастомные эмодзи разрешены не всем. Отказ по содержимому — не повод
    оставить человека вовсе без уведомления, поэтому один раз пробуем послать
    запасной текст.

    Возвращает отправленное сообщение (или None, если не дошло). Само
    сообщение нужно там, где его потом придётся переписать: Telegram не знает
    наших постов, ему нужен номер сообщения в конкретной переписке — см.
    api/routers/news.py, уведомления об удалённой новости. Проверять результат
    как «да/нет» по-прежнему можно: сообщение истинно, None ложно.

    retries/retry_delay — по умолчанию короткие (несколько секунд), чтобы не
    задерживать синхронный ответ API; вызовы из фоновой задачи (см.
    api/routers/event_tasks.py::_send_task_assignment_notifications) могут
    передать более терпеливые значения — они не блокируют ответ пользователю,
    а разовые сбои DNS на сервере бывают длиннее пары секунд."""
    if not telegram_id:
        return None

    bot = bot or get_notifier_bot()
    if bot is None:
        logger.warning("BOT_TOKEN не задан — уведомление не отправлено")
        return None

    for attempt in range(retries + 1):
        try:
            return await bot.send_message(telegram_id, text, parse_mode="HTML", reply_markup=reply_markup)
        except TelegramNetworkError as exc:
            if attempt < retries:
                logger.warning(
                    "Сетевой сбой при отправке уведомления %s (попытка %s/%s): %s",
                    telegram_id, attempt + 1, retries + 1, exc,
                )
                await asyncio.sleep(retry_delay)
                continue
            logger.warning("Не удалось отправить уведомление %s: %s", telegram_id, exc)
            return None
        except TelegramBadRequest as exc:
            # Отказ по содержимому: повторять то же самое бессмысленно, а вот
            # запасной текст может пройти.
            if fallback_text is not None and fallback_text != text:
                logger.warning(
                    "Telegram не принял сообщение для %s (%s) — шлю упрощённый вариант",
                    telegram_id, exc,
                )
                return await notify_telegram(
                    telegram_id, fallback_text, bot=bot, reply_markup=reply_markup,
                    retries=retries, retry_delay=retry_delay,
                )
            logger.warning("Не удалось отправить уведомление %s: %s", telegram_id, exc)
            return None
        except TelegramAPIError as exc:
            logger.warning("Не удалось отправить уведомление %s: %s", telegram_id, exc)
            return None
    return None


async def notify_user(session: AsyncSession, user_id: int, text: str, bot: Bot | None = None) -> bool:
    user = await session.get(User, user_id)
    if user is None:
        return False
    return bool(await notify_telegram(user.telegram_id, text, bot=bot))


async def send_cabinet_welcome(user: User, bot: Bot | None = None) -> None:
    """Два отдельных сообщения при подтверждении личного кабинета (что
    вручную руководителем, что автоматически — api/routers/register.py):
    сперва приветствие по имени-отчеству с подтверждением, что кабинет
    создан, затем отдельным сообщением кнопка на сам кабинет — не одним
    сообщением, так попросили явно."""
    greeting = first_and_patronymic(user.full_name)
    await notify_telegram(
        user.telegram_id,
        f"✅ <b>{greeting}, добро пожаловать в Братство Академистов!</b>\n\nВаш личный кабинет создан.",
        bot=bot,
    )
    if not WEBAPP_URL:
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏛 Открыть кабинет", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )
    await notify_telegram(user.telegram_id, "🏛 Ваш личный кабинет:", bot=bot, reply_markup=keyboard)


async def send_role_assigned_notice(user: User, role: str, bot: Bot | None = None) -> None:
    """При назначении управленческой роли (api/routers/regions.py,
    api/routers/cells.py) — «Имя Отчество, Вы назначены на должность: ...».
    «На должность:» вместо склонения ROLE_LABELS (они в именительном падеже,
    «Вы назначены Руководителем...» потребовало бы творительного — проще и
    надёжнее не склонять вовсе)."""
    greeting = first_and_patronymic(user.full_name)
    label = ROLE_LABELS.get(role, role)
    await notify_telegram(
        user.telegram_id,
        f"🎉 <b>{greeting}, Вы назначены на должность: {label}</b>\n\nТеперь вам доступен кабинет руководителя.",
        bot=bot,
    )


async def send_role_removed_notice(user: User, bot: Bot | None = None) -> None:
    """При снятии управленческой роли (api/routers/regions.py). Зеркало
    send_role_assigned_notice: раз о назначении человек узнаёт от бота, то и о
    снятии он должен узнать оттуда же, а не обнаружить пропажу кабинета сам."""
    greeting = first_and_patronymic(user.full_name)
    await notify_telegram(
        user.telegram_id,
        f"{greeting}, с Вас снята управленческая должность.\n\n"
        "Личный кабинет остаётся при Вас — новости, мероприятия, лобби и магазин "
        "работают как прежде.",
        bot=bot,
    )
