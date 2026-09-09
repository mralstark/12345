"""Проверка текстов перед публикацией: мат не пропускаем, спорное помечаем.

Зачем вообще: пост от имени Братства читают все, и одно неосторожное слово
бьёт по репутации сильнее, чем десять хороших постов её поправят.

Два разных обращения с двумя разными списками:

* мат — публиковать нельзя ни в каком контексте, поэтому блокируем;
* слова про нацизм и фашизм — блокировать нельзя ни в коем случае. Братство
  академическое: посты про Нюрнбергский процесс, про 9 мая, про лекцию по
  истории двадцатого века — обычное дело, и «Победа над фашизмом» обязана
  публиковаться. Поэтому автору показывается вопрос, а руководству уходит
  уведомление. Разница между разбором преступлений и пропагандой — не в
  наборе слов, и машине её не решить.

Сравниваем по началу слова, а не по вхождению подстроки. Вхождение — это
классическая ловушка: «рубля» содержит «бля», «Херсон» содержит «хер». По
началу слова таких срабатываний нет, а окончания («блядь», «блядский»)
ловятся сами собой.

Но и начало слова ловит лишнее — проверено на обычных русских словах:
«мудрость» начинается с «муд», «мандат» — с «манда», «рейхстаг» — с «рейх».
Поэтому есть третий список, исключения: слово, начинающееся с исключения,
совпадением не считается. Без него фильтр заблокировал бы пост со словом
«мудрость».
"""

import re
import unicodedata

KIND_BLOCK = "block"
KIND_WARN = "warn"
KIND_ALLOW = "allow"

# Латинские двойники кириллицы: подмена одной буквы — самый простой способ
# написать мат так, чтобы прямое сравнение его не увидело.
_LOOKALIKE = str.maketrans({
    "a": "а", "b": "ь", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м",
    "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
    # Цифры вместо похожих букв — «6ля», «п3здец».
    "0": "о", "3": "з", "4": "ч", "6": "б",
})

_REPEATS = re.compile(r"(.)\1{2,}")
_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)


def normalize(text: str) -> str:
    """Приводит текст к виду, в котором его можно сравнивать.

    Регистр, ё, латинские двойники и цифры вместо букв, повторы («бллля»).
    Разделители внутри слова намеренно НЕ убираем: «б л я» так не поймается,
    зато «об лямке» не превратится в совпадение. Ложное срабатывание на
    блокировке хуже пропуска — человек не должен доказывать, что он не
    ругался.
    """
    text = unicodedata.normalize("NFKC", text or "").lower().replace("ё", "е")
    text = text.translate(_LOOKALIKE)
    # Три и больше одинаковых подряд — почти наверняка растягивание, сводим к
    # одной букве. Двойные не трогаем: в русском они законны («класс»,
    # «Россия»), и схлопывание портило бы обычные слова.
    return _REPEATS.sub(r"\1", text)


def scan(text: str, rules) -> tuple[list[str], list[str]]:
    """Возвращает (что заблокировать, о чём предупредить).

    rules — пары (корень, вид). Вид: block, warn или allow.
    """
    normalized = normalize(text)
    if not normalized.strip():
        return [], []

    blocked, warned = [], []
    allow, block, warn = [], [], []
    phrases = []

    for root, kind in rules:
        root = normalize(root).strip()
        if not root:
            continue
        if " " in root:
            phrases.append((root, kind))
            continue
        {KIND_ALLOW: allow, KIND_BLOCK: block, KIND_WARN: warn}.get(kind, warn).append(root)

    # Устойчивые выражения ищем целиком: по отдельности «зиг» и «хайль»
    # ничего не значат, а вместе значат вполне определённое.
    for root, kind in phrases:
        if root in normalized:
            (blocked if kind == KIND_BLOCK else warned).append(root)

    for word in _WORDS.findall(normalized):
        if any(word.startswith(safe) for safe in allow):
            continue
        hit = next((r for r in block if word.startswith(r)), None)
        if hit is not None:
            blocked.append(hit)
            continue
        hit = next((r for r in warn if word.startswith(r)), None)
        if hit is not None:
            warned.append(hit)

    # Без повторов и в устойчивом порядке — сообщение человеку не должно
    # меняться от того, сколько раз он повторил слово.
    return sorted(set(blocked)), sorted(set(warned))


BLOCK_MESSAGE = (
    "В тексте есть нецензурное слово — от имени Братства так публиковать нельзя. "
    "Поправьте и попробуйте снова."
)


async def rules_from_db(session) -> list[tuple[str, str]]:
    """Перечень из базы. Отдельной функцией, чтобы места вызова не знали, как
    он хранится, — а хранится он в базе именно затем, чтобы меняться без
    выкладки."""
    from sqlalchemy import select

    from database.models import ModerationWord

    rows = (await session.execute(select(ModerationWord))).scalars().all()
    return [(row.root, row.kind) for row in rows]


async def check(session, text: str) -> list[str]:
    """Проверяет текст и бросает ValueError, если публиковать нельзя.

    Возвращает список того, о чём стоит предупредить, — пустой, если всё
    чисто. Решение, что делать с предупреждением, принимает вызывающий: у
    поста это уведомление руководству, у профиля пока ничего.
    """
    blocked, warned = scan(text, await rules_from_db(session))
    if blocked:
        raise ValueError(BLOCK_MESSAGE)
    return warned
