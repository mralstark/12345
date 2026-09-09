"""Проверка текстов перед публикацией.

Главное, за чем тут следят: фильтр не должен мешать обычным постам. Ложное
срабатывание на блокировке хуже пропуска — человек не должен доказывать, что
он не ругался. Поэтому обычных слов в проверках больше, чем ругательных.
"""

import pytest

from database.models import ModerationWord
from services.moderation import KIND_ALLOW, KIND_BLOCK, KIND_WARN, scan
from tests.conftest import login

RULES = [
    ("хуй", KIND_BLOCK), ("пизд", KIND_BLOCK), ("еб", KIND_BLOCK),
    ("бля", KIND_BLOCK), ("муд", KIND_BLOCK), ("манда", KIND_BLOCK),
    ("шлюх", KIND_BLOCK), ("залуп", KIND_BLOCK),
    ("сволоч", KIND_WARN), ("ублюд", KIND_WARN),
    ("гитлер", KIND_WARN), ("нацизм", KIND_WARN), ("фашизм", KIND_WARN),
    ("рейх", KIND_WARN), ("зиг хайль", KIND_WARN),
    ("мудр", KIND_ALLOW), ("мандат", KIND_ALLOW), ("мандарин", KIND_ALLOW),
    ("бляха", KIND_ALLOW), ("бляш", KIND_ALLOW), ("рейхстаг", KIND_ALLOW),
]


@pytest.mark.parametrize("text", [
    "Собрание отделения в четверг в 19:00",
    "Мудрость поколений — наша опора",          # «муд»
    "Получили мандат на проведение съезда",      # «манда»
    "Купили мандарины на чаепитие",              # «манда»
    "Экскурсия в Рейхстаг во время поездки",     # «рейх»
    "Сдали 5000 рубля в кассу отделения",        # «бля» внутри слова
    "Поездка в Херсон",                          # «хер» внутри слова
    "Купили сукно для оформления зала",          # «сук» внутри слова
    "Оглобля сломалась на реконструкции",        # «бля» внутри слова
])
def test_ordinary_posts_pass(text):
    """Обычный пост обязан проходить. Это важнее всего остального: фильтр,
    который мешает работать, отключат целиком — и толку от него не будет."""
    blocked, warned = scan(text, RULES)
    assert blocked == [], f"{text!r} заблокирован по {blocked}"
    assert warned == [], f"{text!r} помечен по {warned}"


@pytest.mark.parametrize("text", [
    "Да он же мудак",
    "Полная хуйня получилась",
    "Блядь, опять перенесли",
])
def test_profanity_is_blocked(text):
    blocked, _ = scan(text, RULES)
    assert blocked, f"{text!r} прошёл незамеченным"


def test_letter_substitution_is_caught():
    """Подмена буквы латиницей или цифрой — самый простой обход."""
    blocked, _ = scan("Полная хyйня", RULES)          # латинская y
    assert blocked
    blocked, _ = scan("6лядь какая", RULES)           # цифра вместо буквы
    assert blocked


def test_stretched_letters_are_caught():
    blocked, _ = scan("Ну блллля", RULES)
    assert blocked


def test_history_posts_are_warned_not_blocked():
    """Братство академическое: пост про историю обязан выйти. Мы только
    сообщаем руководству, что он вышел."""
    blocked, warned = scan("Лекция о преступлениях нацизма и суде над ними", RULES)
    assert blocked == []
    assert "нацизм" in warned


def test_victory_day_post_publishes():
    """«Победа над фашизмом» — то, что вы напишете 9 мая. Блокировать это
    было бы не просто глупо, а оскорбительно."""
    blocked, warned = scan("Поздравляем с годовщиной Победы над фашизмом!", RULES)
    assert blocked == []
    assert warned == ["фашизм"]


def test_phrase_is_matched_whole():
    """По отдельности «зиг» и «хайль» ничего не значат."""
    assert scan("Зиг это архитектурный термин", RULES) == ([], [])
    _, warned = scan("Кричали зиг хайль", RULES)
    assert "зиг хайль" in warned


def test_repeats_do_not_multiply_the_message():
    """Человеку показываем, что не так, а не сколько раз он это повторил."""
    blocked, _ = scan("мудак мудак мудак", RULES)
    assert blocked == ["муд"]


def test_empty_text_is_fine():
    assert scan("", RULES) == ([], [])
    assert scan("   ", RULES) == ([], [])


def test_normalize_leaves_spacing_alone():
    """Разделители внутри слова не убираем: «б л я» не поймается, зато
    «об лямке» не превратится в совпадение."""
    assert scan("Речь об лямке рюкзака", RULES) == ([], [])


# --- Проверка на живых путях публикации ---------------------------------------


async def _seed_words(session):
    session.add_all([
        ModerationWord(root="муд", kind=KIND_BLOCK),
        ModerationWord(root="мудр", kind=KIND_ALLOW),
        ModerationWord(root="фашизм", kind=KIND_WARN),
    ])
    await session.commit()


async def test_post_with_profanity_is_not_published(client, world, session):
    """Мат до ленты не доходит вовсе."""
    await _seed_words(session)
    login(world["leader_moscow"])

    response = await client.post("/api/news", data={"text": "Он полный мудак"})
    assert response.status_code == 400
    assert "нецензурное" in response.json()["detail"]
    assert (await client.get("/api/news")).json()["items"] == []


async def test_ordinary_post_still_publishes(client, world, session):
    """Слово «мудрость» обязано проходить — иначе фильтр мешает работать."""
    await _seed_words(session)
    login(world["leader_moscow"])

    assert (await client.post(
        "/api/news", data={"text": "Мудрость поколений — наша опора"}
    )).status_code == 200


async def test_history_post_publishes_and_leadership_is_told(client, world, session, monkeypatch):
    """Пост про историю выходит, но руководство узнаёт о нём."""
    await _seed_words(session)
    sent = []

    async def fake_send(telegram_id, text, **kwargs):
        sent.append((telegram_id, text))
        return None

    monkeypatch.setattr("api.routers.news.notify_telegram", fake_send)
    login(world["leader_moscow"])

    assert (await client.post(
        "/api/news", data={"text": "Поздравляем с Победой над фашизмом!"}
    )).status_code == 200

    warnings = [t for _, t in sent if "требующими внимания" in t]
    assert warnings, "руководство не узнало о посте"
    assert "фашизм" in warnings[0]


async def test_comment_with_profanity_is_rejected(client, world, session):
    """Проверка мата в комментарии жива и ждёт, когда комментарии откроют
    обратно (services/news.py::COMMENTS_OPEN). Сейчас до неё не доходит:
    закрытый эндпоинт отказывает раньше."""
    await _seed_words(session)
    login(world["leader_moscow"])
    post_id = (await client.post("/api/news", data={"text": "Собрание"})).json()["id"]

    closed = await client.post(f"/api/news/{post_id}/comments", json={"text": "мудак"})
    assert closed.status_code == 403

    from services.news import add_comment
    from utils.access import AccessDenied  # noqa: F401  — проверяем не его

    with pytest.raises(ValueError):
        await add_comment(session, world["leader_moscow"], post_id, "мудак")


async def test_profile_about_with_profanity_is_rejected(client, world, session):
    """«О себе» видит каждый, кто откроет профиль, — проверяем так же."""
    from database.models import Member, ROLE_PARTICIPANT, User

    await _seed_words(session)
    member = Member(region_id=world["moscow"].id, full_name="Иванов Иван")
    session.add(member)
    await session.flush()
    user = User(full_name="Иванов Иван", role=ROLE_PARTICIPANT, telegram_id=3001, member_id=member.id)
    session.add(user)
    await session.commit()
    await session.refresh(user)

    login(user)
    response = await client.patch("/api/profile/me", json={"about": "Я тот ещё мудак"})
    assert response.status_code == 400

    ok = await client.patch("/api/profile/me", json={"about": "Ценю мудрость и историю"})
    assert ok.status_code == 200


# --- Согласованный перечень целиком -------------------------------------------
# Проверяем не выдуманные правила, а тот самый список, который уходит в базу.


def _real_rules():
    from scripts.seed_moderation_words import ALLOW, BLOCK, WARN

    return ([(w, KIND_BLOCK) for w in BLOCK] + [(w, KIND_WARN) for w in WARN]
            + [(w, KIND_ALLOW) for w in ALLOW])


@pytest.mark.parametrize("word", [
    "Херсон", "херсонский", "херувим",     # «хер»
    "сукно", "суконный", "Сукачёв",        # «сука»
    "чмокнуть",                            # «чмо»
    "мудрость", "мандат", "мандарин", "Рейхстаг",
    "уборка", "убеждение",                 # «уеб» их не задевает
    "объезд", "съезд", "разъезд", "приезд", "доезд",  # приставочные формы
])
def test_real_list_lets_ordinary_words_through(word):
    """Самая дорогая ошибка — заблокировать обычное слово. «Съезд» и
    «Рейхстаг» у Братства встречаются постоянно."""
    blocked, warned = scan(word, _real_rules())
    assert blocked == [], f"{word!r} заблокирован по {blocked}"
    assert warned == [], f"{word!r} помечен по {warned}"


@pytest.mark.parametrize("word", [
    "хер", "херня",
    "сука", "сучка", "говно", "дерьмо", "жопа", "срать", "чмо",
])
def test_real_list_blocks_vulgar_words(word):
    blocked, _ = scan(word, _real_rules())
    assert blocked, f"{word!r} прошёл незамеченным"


@pytest.mark.parametrize("word", ["долбоёб", "заебал", "уебал", "наебал", "съебался", "приебался"])
def test_mat_inside_compound_words_is_caught(word):
    """Сравнение идёт по началу слова, поэтому корень в середине так не
    поймать: «долбоёб» проходил свободно, пока приставочных форм не было."""
    blocked, _ = scan(word, _real_rules())
    assert blocked, f"{word!r} прошёл — приставочная форма не покрыта"


@pytest.mark.parametrize("word", ["мразь", "тварь", "урод", "придурок", "сволочь", "ублюдок"])
def test_insults_only_warn(word):
    """Брань не мат: пост выходит, руководство узнаёт. Так решило руководство."""
    blocked, warned = scan(word, _real_rules())
    assert blocked == [], f"{word!r} заблокирован, а должен только предупреждать"
    assert warned, f"{word!r} прошёл вовсе незамеченным"
