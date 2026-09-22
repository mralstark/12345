from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from config import WEBAPP_URL
from database.models import ROLE_FEDERAL, ROLE_LEADER, ROLE_SUPERUSER, SUPERVISOR_ROLES, User
from utils.permissions import has_any_role, has_role
from utils.users import IMPERSONATOR_ROLES

# Приветствие для незнакомого /start (план «Снизу вверх», §1) — два разных
# пути, не один: настоящий отбор идёт на сайте (вне этого проекта), а
# «Создать личный кабинет» — для уже принятого человека, открывает нашу же
# форму регистрации в Mini App (см. api/routers/register.py). region_id, если
# известен (переход по APPLY_-ссылке региона, handlers/apply.py), пропускает
# шаг выбора отделения в форме.
WELCOME_TEXT = (
    '<tg-emoji emoji-id="5208770610980724566">🏛</tg-emoji> <b>Личный кабинет Братства Академистов</b>\n\n'
    "Если вы ещё не подавали заявку на вступление — начните с сайта: там анкета для заполнения. "
    "После того как ваша кандидатура будет одобрена, вы сможете вернуться сюда для создания кабинета.\n\n"
    "Если вы уже академист — создайте личный кабинет прямо сейчас, нажав кнопку ниже."
)


def register_welcome_keyboard(region_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🌐 Подать заявку на сайте", url="https://academists.ru/form")]]
    if WEBAPP_URL:
        register_url = WEBAPP_URL + "?view=register"
        if region_id is not None:
            register_url += "&region_id=" + str(region_id)
        rows.append(
            [InlineKeyboardButton(text="📝 Создать личный кабинет", web_app=WebAppInfo(url=register_url))]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu_keyboard(user: User, pending_applications: int = 0) -> InlineKeyboardMarkup:
    """Главное меню бота. Тяжёлые разделы (состав, финансы, мероприятия,
    документы, задачи, новости, быстрая запись, аналитика, отчёты) —
    целиком в Mini App; в боте остаются только действия, которых там либо нет
    вовсе (подтверждения личных кабинетов, «войти как»), либо которые нужны
    до входа в кабинет. Создание/переименование/удаление регионов, назначение
    управленческих ролей — вкладка «Регионы» в Mini App (api/routers/regions.py,
    api/routers/cells.py), не бот — служебная кнопка «⚙️ Управление» убрана
    совсем (handlers/manage_accounts.py, удалён, был не нужен).

    Кнопки строятся по роли user — а это может быть не реальный вызывающий,
    а тот, кого он сейчас «смотрит» (см. utils.users.resolve_user), поэтому
    кабинет админа в режиме impersonation выглядит и ведёт себя ровно как
    у выбранного человека. Кнопка выхода — по _impersonated_by, отдельной
    строкой поверх; «Войти как...» тоже только для реальной личности админа
    (это не то, что умеет показываемый человек) — IMPERSONATOR_ROLES, те же
    люди, что и в handlers/impersonate.py. Все остальные кнопки — по роли
    user без оглядки на impersonation: то, что видит показываемый человек,
    должно быть видно и во время просмотра за него."""
    rows: list[list[InlineKeyboardButton]] = []
    impersonated_by = getattr(user, "_impersonated_by", None)

    if impersonated_by is not None:
        rows.append([InlineKeyboardButton(text="🔙 Выйти из режима просмотра", callback_data="menu:stop_impersonate")])

    if WEBAPP_URL:
        # Один вход вместо двух. Спрашивать человека, с чего он сидит, было
        # незачем: приложение и так видит ширину окна и раздвигает вёрстку
        # само (webapp/app.js, styles.css .layout-desktop). Открывается всегда
        # во весь экран.
        rows.append([InlineKeyboardButton(text="📂 Открыть кабинет", web_app=WebAppInfo(url=WEBAPP_URL))])

    # Подтверждения личных кабинетов (handlers/apply.py) — временно у
    # федерального координатора, не у руководителя региона (план «Убираем
    # технического superuser» — руководители пока не подтверждают анкеты
    # сами, см. config.PRIMARY_REVIEWER_FULL_NAME).
    if user.role == ROLE_LEADER or has_any_role(user, SUPERVISOR_ROLES):
        applications_label = f"📝 Подтверждения ({pending_applications})" if pending_applications else "📝 Подтверждения"
        rows.append([InlineKeyboardButton(text=applications_label, callback_data="apply:list")])

    # Ссылка-приглашение — по-прежнему у руководителя: не про подтверждение
    # анкет, просто удобный шорткат с уже выбранным регионом в форме
    # саморегистрации (см. handlers/apply.py::show_application_link).
    if user.role == ROLE_LEADER:
        rows.append([InlineKeyboardButton(text="🔗 Ссылка для приглашения", callback_data="apply:link")])

    if impersonated_by is None and has_role(user, ROLE_SUPERUSER):
        rows.append([InlineKeyboardButton(text="🕵 Войти как...", callback_data="menu:impersonate")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main")]]
    )
