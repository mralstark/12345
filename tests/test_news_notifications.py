"""Круг адресатов уведомления о новости.

Главное, что тут проверяется: кому уходит уведомление, решает подпись под
постом, а не роль автора сама по себе. Подпись «Братство Академистов» обещает
читателю, что говорят от имени всего Братства, — уведомление и уходит всем;
«Академисты | Москва» — что от имени отделения, и дальше отделения не идёт.
Разойдись эти два круга, подпись обещала бы одно, а рассылка делала другое.
"""

from sqlalchemy import select

from database.models import ROLE_PARTICIPANT, Member, NewsNotification, User
from services.news import create_news, news_audience_telegram_ids
from tests.conftest import login


async def _participant(session, region_id, name, telegram_id, cell_id=None):
    """Участник с личным кабинетом: карточка в составе плюс вход в систему."""
    member = Member(region_id=region_id, full_name=name, cell_id=cell_id)
    session.add(member)
    await session.flush()
    user = User(full_name=name, role=ROLE_PARTICIPANT, member_id=member.id, telegram_id=telegram_id)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


class _SentMessage:
    """Что возвращает Telegram после отправки: у сообщения есть номер и чат."""

    def __init__(self, chat_id, message_id):
        self.chat = type("Chat", (), {"id": chat_id})()
        self.message_id = message_id


def _capture_sent(monkeypatch):
    """Подменяет отправку в Telegram и копит, кому и что ушло.

    Возвращает не «да», а объект сообщения: настоящая отправка отдаёт именно
    его — по номеру сообщения потом переписывается уведомление об удалённой
    новости. Подставка обязана вести себя так же, иначе тест проверяет не то,
    что работает на самом деле.
    """
    sent = []
    counter = {"n": 900}

    async def fake_send(telegram_id, text, **kwargs):
        sent.append((telegram_id, text))
        counter["n"] += 1
        return _SentMessage(telegram_id, counter["n"])

    monkeypatch.setattr("api.routers.news.notify_telegram", fake_send)
    return sent


async def test_bratstvo_post_notifies_everyone(world, session):
    """Пост от Братства получают все — и чужой регион тоже."""
    moscow = await _participant(session, world["moscow"].id, "Москвич Один", 2001)
    tula = await _participant(session, world["tula"].id, "Туляк Один", 2002)

    post = await create_news(session, world["federal"], "Съезд в декабре", official=True)
    assert post.byline == "Братство Академистов"

    audience = await news_audience_telegram_ids(session, post)
    assert moscow.telegram_id in audience
    assert tula.telegram_id in audience
    # Автора среди адресатов нет — он и так знает, что опубликовал.
    assert world["federal"].telegram_id not in audience


async def test_region_post_stays_inside_its_region(world, session):
    """Пост городского отделения не уходит за пределы его региона."""
    moscow = await _participant(session, world["moscow"].id, "Москвич Два", 2003)
    tula = await _participant(session, world["tula"].id, "Туляк Два", 2004)

    post = await create_news(session, world["leader_moscow"], "Сбор в среду", official=True)
    assert post.byline == "Академисты | Москва"

    audience = await news_audience_telegram_ids(session, post)
    assert moscow.telegram_id in audience
    assert tula.telegram_id not in audience


async def test_cell_post_stays_inside_its_cell(world, session):
    """У вузовского отделения круг ýже региона — своя ячейка."""
    in_cell = await _participant(session, world["moscow"].id, "Мгимошник", 2005,
                                 cell_id=world["mgimo"].id)
    same_region = await _participant(session, world["moscow"].id, "Москвич Три", 2006)

    post = await create_news(session, world["cell_leader"], "Дебаты в пятницу", official=True)
    assert post.byline == "Академисты | МГИМО"

    audience = await news_audience_telegram_ids(session, post)
    assert in_cell.telegram_id in audience
    # Тот же регион, но вне ячейки — не его аудитория.
    assert same_region.telegram_id not in audience


async def test_personal_post_notifies_nobody(world, session):
    """Личный пост не рассылается: лента открыта всем, и уведомление с каждого
    личного поста было бы не новостью, а спамом."""
    await _participant(session, world["moscow"].id, "Москвич Четыре", 2007)

    # Тот же руководитель, но из личного кабинета — подпись своим именем.
    post = await create_news(session, world["leader_moscow"], "Ищу попутчиков", official=False)
    assert post.byline == "Иванов Иван"

    assert await news_audience_telegram_ids(session, post) == []


async def test_disabled_and_cabinetless_are_not_addressed(world, session):
    """Отключённый аккаунт и карточка без входа в систему адресатами не бывают."""
    disabled = await _participant(session, world["moscow"].id, "Отключённый", 2008)
    disabled.is_active = False
    # Человек в составе, но кабинета у него нет — уведомлять некуда.
    session.add(Member(region_id=world["moscow"].id, full_name="Без Кабинета"))
    await session.commit()

    post = await create_news(session, world["federal"], "Общая новость", official=True)
    assert disabled.telegram_id not in await news_audience_telegram_ids(session, post)


async def test_publishing_through_api_sends(client, world, session, monkeypatch):
    """Сквозная проверка обвязки: публикация ставит фоновую задачу, та открывает
    свою сессию и доходит до отправки. Сам Telegram не трогаем."""
    reader = await _participant(session, world["moscow"].id, "Москвич Пять", 2009)
    sent = _capture_sent(monkeypatch)

    login(world["leader_moscow"])
    assert (await client.post(
        "/api/news", data={"text": "Сбор в среду", "official": "true"}
    )).status_code == 200

    assert [telegram_id for telegram_id, _ in sent] == [reader.telegram_id]
    assert "Академисты | Москва" in sent[0][1]
    assert "Сбор в среду" in sent[0][1]


async def test_angle_brackets_in_post_do_not_break_sending(client, world, session, monkeypatch):
    """Текст поста уходит с parse_mode="HTML": неэкранированная скобка сорвала
    бы отправку сразу всем адресатам."""
    await _participant(session, world["moscow"].id, "Москвич Шесть", 2010)
    sent = _capture_sent(monkeypatch)

    login(world["federal"])
    await client.post("/api/news", data={"text": "Разбор <b> и <script>", "official": "true"})

    # От Братства уведомление уходит всем, поэтому смотрим не на число
    # адресатов, а на то, что скобки во всех сообщениях обезврежены.
    assert sent
    assert all("&lt;b&gt;" in text and "<script>" not in text for _, text in sent)


async def test_long_post_is_trimmed_in_notification(client, world, session, monkeypatch):
    """Уведомление — приглашение открыть кабинет, а не замена ленте: длинный
    пост подрезается, иначе упёрся бы в предел длины сообщения Telegram."""
    await _participant(session, world["moscow"].id, "Москвич Семь", 2011)
    sent = _capture_sent(monkeypatch)

    login(world["federal"])
    await client.post("/api/news", data={"text": "А" * 1200, "official": "true"})

    assert sent
    assert all(text.endswith("…") and len(text) < 600 for _, text in sent)


async def test_sent_notifications_are_remembered(client, world, session, monkeypatch):
    """Номер отправленного сообщения надо сохранить сразу: потом, когда новость
    удалят, взять его будет уже неоткуда — Telegram не знает наших постов."""
    reader = await _participant(session, world["moscow"].id, "Москвич Восемь", 2012)

    counter = {"n": 500}

    async def fake_send(telegram_id, text, **kwargs):
        counter["n"] += 1
        return _SentMessage(telegram_id, counter["n"])

    monkeypatch.setattr("api.routers.news.notify_telegram", fake_send)

    login(world["leader_moscow"])
    post_id = (await client.post(
        "/api/news", data={"text": "Сбор в среду", "official": "true"}
    )).json()["id"]

    rows = (await session.execute(
        select(NewsNotification).where(NewsNotification.post_id == post_id)
    )).scalars().all()
    assert [(r.chat_id, r.message_id) for r in rows] == [(reader.telegram_id, 501)]


async def test_deleting_news_marks_notification_instead_of_leaving_it(
    client, world, session, monkeypatch
):
    """Удалили новость — уведомление в боте не должно остаться как ни в чём не
    бывало. Переписываем, а не стираем: стереть Telegram разрешает боту только
    в первые 48 часов, а переписать — когда угодно."""
    await _participant(session, world["moscow"].id, "Москвич Девять", 2013)

    async def fake_send(telegram_id, text, **kwargs):
        return _SentMessage(telegram_id, 777)

    monkeypatch.setattr("api.routers.news.notify_telegram", fake_send)

    edited = []

    class FakeBot:
        async def edit_message_text(self, text, chat_id, message_id, **kwargs):
            edited.append((chat_id, message_id, text, kwargs.get("reply_markup", "нет ключа")))

    monkeypatch.setattr("api.routers.news.get_notifier_bot", lambda: FakeBot())

    login(world["leader_moscow"])
    post_id = (await client.post(
        "/api/news", data={"text": "Сбор в среду", "official": "true"}
    )).json()["id"]

    assert (await client.delete(f"/api/news/{post_id}")).status_code == 200

    assert edited, "уведомление осталось нетронутым"
    chat_id, message_id, text, markup = edited[0]
    assert (chat_id, message_id) == (2013, 777)
    assert "Новость удалена" in text
    # Подпись остаётся: человеку видно, чьё именно объявление убрали.
    assert "Академисты | Москва" in text
    # Кнопку убираем — открывать в кабинете больше нечего.
    assert markup is None


async def test_personal_post_deletion_touches_nothing(client, world, session, monkeypatch):
    """Личный пост никого не уведомлял — значит и переписывать нечего."""
    await _participant(session, world["moscow"].id, "Москвич Десять", 2014)

    edited = []

    class FakeBot:
        async def edit_message_text(self, **kwargs):
            edited.append(kwargs)

    monkeypatch.setattr("api.routers.news.get_notifier_bot", lambda: FakeBot())

    login(world["leader_moscow"])
    post_id = (await client.post("/api/news", data={"text": "Ищу попутчиков"})).json()["id"]
    assert (await client.delete(f"/api/news/{post_id}")).status_code == 200

    assert edited == []


async def test_bratstvo_notification_carries_its_own_mark(client, world, session, monkeypatch):
    """Уведомление от Братства помечается своим знаком — кастомным эмодзи."""
    from api.routers.news import BRATSTVO_EMOJI_ID

    await _participant(session, world["moscow"].id, "Москвич Одиннадцать", 2015)
    sent = _capture_sent(monkeypatch)

    login(world["federal"])
    await client.post("/api/news", data={"text": "Съезд в декабре", "official": "true"})

    assert sent
    assert all(BRATSTVO_EMOJI_ID in text for _, text in sent)


async def test_otdelenie_notification_stays_plain(client, world, session, monkeypatch):
    """У отделения своего знака нет — там обычный значок, как и раньше."""
    from api.routers.news import BRATSTVO_EMOJI_ID

    await _participant(session, world["moscow"].id, "Москвич Двенадцать", 2016)
    sent = _capture_sent(monkeypatch)

    login(world["leader_moscow"])
    await client.post("/api/news", data={"text": "Сбор в среду", "official": "true"})

    assert sent
    assert all(BRATSTVO_EMOJI_ID not in text for _, text in sent)


async def test_rejected_custom_emoji_falls_back_to_plain(world, session, monkeypatch):
    """Кастомные эмодзи разрешены не каждому боту. Если Telegram откажет по
    содержимому, человек всё равно должен получить уведомление — обычным
    значком, а не остаться вовсе без него."""
    from aiogram.exceptions import TelegramBadRequest

    from utils import notify as notify_module

    attempts = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kwargs):
            attempts.append(text)
            if "tg-emoji" in text:
                raise TelegramBadRequest(method=None, message="custom emoji is not allowed")
            return _SentMessage(chat_id, 42)

    monkeypatch.setattr(notify_module, "get_notifier_bot", lambda: FakeBot())

    result = await notify_module.notify_telegram(
        555,
        '<tg-emoji emoji-id="1">📣</tg-emoji> <b>Братство</b>',
        fallback_text="📣 <b>Братство</b>",
    )

    assert result is not None, "человек остался без уведомления"
    assert len(attempts) == 2, "запасной вариант не отправлялся"
    assert "tg-emoji" not in attempts[1]
