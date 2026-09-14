"""Схема данных волны 1 (см. ТЗ §15).

Все денежные суммы — целые копейки (int), как в боте-доноре.
Роли и статусы хранятся строками: их немного, они читаемы в дампе БД,
и добавление новых ролей в следующих волнах не потребует миграции типа.
"""

from datetime import date as date_
from datetime import datetime
from datetime import time as time_

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# --- Роли (ТЗ §3) -----------------------------------------------------------
ROLE_SUPERUSER = "superuser"
ROLE_FEDERAL = "federal"
ROLE_COORDINATOR = "coordinator"
ROLE_LEADER = "leader"
# Руководитель вузовской ячейки — на уровень ниже руководителя региона,
# отвечает за свой вуз внутри региона (не отдельная организационная волна,
# а сужение того же региона до одной ячейки — см. utils/access.py).
ROLE_CELL_LEADER = "cell_leader"
# Личный кабинет без управленческих прав — роль, которую получает каждый,
# кто прошёл саморегистрацию (services/applications.py::approve_application).
# Намеренно не входит в SUPERVISOR_ROLES и не упомянута ни в одной ветке
# utils/access.py — не совпав ни с одним if/elif, получает пустой доступ
# по умолчанию.
ROLE_PARTICIPANT = "participant"

ROLE_LABELS = {
    ROLE_SUPERUSER: "Технический superuser",
    ROLE_FEDERAL: "Федеральный координатор",
    ROLE_COORDINATOR: "Координатор регионов",
    ROLE_LEADER: "Руководитель регионального отделения",
    ROLE_CELL_LEADER: "Руководитель вузовской ячейки",
    ROLE_PARTICIPANT: "Участник Братства",
}

# Роли, которые видят несколько регионов и могут рассылать/ставить задачи вниз.
SUPERVISOR_ROLES = (ROLE_SUPERUSER, ROLE_FEDERAL, ROLE_COORDINATOR)

# --- Статусы состава (ТЗ §6) ------------------------------------------------
MEMBER_STATUS_ACTIVIST = "activist"
MEMBER_STATUS_MEMBER = "member"
MEMBER_STATUS_ALUMNI = "alumni"

# Значение в базе осталось "activist" — переименование чисто словесное, и
# переписывать ради него все записи не за чем. Видит человек «Корпорант».
MEMBER_STATUS_LABELS = {
    MEMBER_STATUS_ACTIVIST: "Корпорант",
    MEMBER_STATUS_MEMBER: "Член Братства",
    MEMBER_STATUS_ALUMNI: "Выпускник",
}

# --- Уровень обучения (форма регистрации, план «Снизу вверх») ---------------
EDUCATION_LEVEL_BACHELOR = "bachelor"
EDUCATION_LEVEL_MASTER = "master"
EDUCATION_LEVEL_POSTGRADUATE = "postgraduate"
EDUCATION_LEVEL_RESIDENCY = "residency"

EDUCATION_LEVEL_LABELS = {
    EDUCATION_LEVEL_BACHELOR: "Бакалавриат",
    EDUCATION_LEVEL_MASTER: "Магистратура",
    EDUCATION_LEVEL_POSTGRADUATE: "Аспирантура",
    EDUCATION_LEVEL_RESIDENCY: "Ординатура",
}

# --- Статусы мероприятий (ТЗ §8) --------------------------------------------
EVENT_STATUS_PLANNED = "planned"
EVENT_STATUS_DONE = "done"
EVENT_STATUS_CANCELLED = "cancelled"

# Задача мероприятия говорит на языке задач, а не мероприятий: «Запланировано»
# — это про событие в календаре, а не про работу, которую кто-то делает.
# Значения в базе остались прежними (planned/done/cancelled), меняются только
# подписи; in_progress добавлен — без него человек не мог сказать, что взялся.
EVENT_TASK_STATUS_LABELS = {
    "planned": "Новая",
    "in_progress": "В работе",
    "done": "Выполнена",
    "cancelled": "Отменена",
}

EVENT_STATUS_LABELS = {
    EVENT_STATUS_PLANNED: "Запланировано",
    EVENT_STATUS_DONE: "Проведено",
    EVENT_STATUS_CANCELLED: "Отменено",
}

# --- Статусы задач (ТЗ §10.2) -----------------------------------------------
TASK_STATUS_NEW = "new"
TASK_STATUS_IN_PROGRESS = "in_progress"
TASK_STATUS_DONE = "done"
TASK_STATUS_OVERDUE = "overdue"

TASK_STATUS_LABELS = {
    TASK_STATUS_NEW: "Новая",
    TASK_STATUS_IN_PROGRESS: "В работе",
    TASK_STATUS_DONE: "Выполнена",
    TASK_STATUS_OVERDUE: "Просрочена",
}

# Задачи, по которым дедлайн ещё имеет смысл отслеживать.
TASK_STATUSES_OPEN = (TASK_STATUS_NEW, TASK_STATUS_IN_PROGRESS)

# --- Состояния заявки на вступление (публичная саморегистрация) ------------
APPLICATION_STATE_PENDING = "pending"
APPLICATION_STATE_APPROVED = "approved"
APPLICATION_STATE_REJECTED = "rejected"

APPLICATION_STATE_LABELS = {
    APPLICATION_STATE_PENDING: "На рассмотрении",
    APPLICATION_STATE_APPROVED: "Одобрена",
    APPLICATION_STATE_REJECTED: "Отклонена",
}


class User(Base):
    """Человек с ролью в системе. telegram_id появляется только через
    саморегистрацию (services/applications.py::approve_application) —
    управленческая роль назначается уже существующему аккаунту, никогда не
    создаёт новый с нуля (services/admin_actions.py::_resolve_or_promote)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Режим «войти как» (utils/users.resolve_user, IMPERSONATOR_ROLES) — кого
    # пользователь сейчас видит вместо себя. Осмысленно только у ролей из
    # IMPERSONATOR_ROLES (superuser, federal); у всех остальных всегда NULL.
    # Не рекурсивно: если цель сама окажется с непустым полем, это поле у
    # неё игнорируется — глубина ровно один уровень.
    view_as_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Человек из «Состава», за которым стоит этот аккаунт. Инвариант: заполнено
    # у всех ролей, кроме superuser (техническая роль, в составе не значится).
    # Даёт «Личный кабинет» (api/routers/profile.py) — свои же данные состава,
    # редактируемые самим человеком. unique=True — на одного человека максимум
    # один аккаунт (страховка на уровне БД поверх проверки в сервисе).
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), unique=True, nullable=True)

    led_region: Mapped["Region | None"] = relationship(
        back_populates="leader", foreign_keys="Region.leader_user_id", uselist=False
    )
    coordinated_regions: Mapped[list["CoordinatorRegion"]] = relationship(
        back_populates="coordinator", cascade="all, delete-orphan"
    )
    member: Mapped["Member | None"] = relationship(back_populates="user", foreign_keys=[member_id])


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    # Название в родительном падеже («Красноярска», «Санкт-Петербурга») — для
    # подписи руководителя «Руководитель Академистов {genitive_name}».
    # Программно склонять русские топонимы ненадёжно (пример: «Москва» →
    # «Москвы»), поэтому админ вводит один раз при создании региона
    # (scripts/admin.py region-add --genitive). Пусто — используется name как есть.
    genitive_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Многоразовый код на вход в форму самостоятельной заявки на вступление
    # (t.me/<bot>?start=APPLY_XXXX) — один на регион, живёт, пока руководитель
    # его не обновит (см. utils/invites.py, services/admin_actions.py).
    application_code: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    # Руководитель отделения — один на регион (ТЗ §3).
    leader_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    leader: Mapped["User | None"] = relationship(back_populates="led_region", foreign_keys=[leader_user_id])
    members: Mapped[list["Member"]] = relationship(back_populates="region", cascade="all, delete-orphan")
    categories: Mapped[list["Category"]] = relationship(back_populates="region", cascade="all, delete-orphan")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="region", cascade="all, delete-orphan")
    events: Mapped[list["Event"]] = relationship(back_populates="region", cascade="all, delete-orphan")
    documents: Mapped[list["Document"]] = relationship(back_populates="region", cascade="all, delete-orphan")
    cells: Mapped[list["UniversityCell"]] = relationship(back_populates="region", cascade="all, delete-orphan")


class University(Base):
    """Каталог вузов — общий на всю систему, не привязан к региону (сам вуз
    один, а ячеек с его именем в разных регионах может быть несколько).
    Список свой, растёт сам: нашли по имени — вернули существующую запись,
    не нашли — завели новую (см. api/routers/universities.py). Внешней базы
    вузов с пригодным для интеграции API в интернете нет — только портал
    Рособрнадзора без экспорта, поэтому решили не тянуть внешнюю зависимость."""

    __tablename__ = "universities"
    __table_args__ = (Index("uq_universities_name_ci", text("lower(name)"), unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    # Регион/город, к которому относится вуз — для фильтрации при регистрации
    # (человек выбрал отделение, видит только вузы этого города). Nullable:
    # задел на будущие регионы без готового списка и на старые записи каталога,
    # которые ещё предстоит сопоставить (scripts/migrate_university_region.py).
    region_id: Mapped[int | None] = mapped_column(ForeignKey("regions.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UniversityCell(Base):
    """Вузовская ячейка внутри региона — не отдельная волна, а сужение того же
    региона до одного вуза (см. utils/access.py: ROLE_CELL_LEADER видит только
    свою ячейку, но в границах региона родителя). Количество ячеек на регион
    произвольное, в некоторых регионах их вовсе нет.

    В обычном случае возникает сама: человеку в «Составе» проставили вуз —
    utils/university_cells.resolve_member_cell находит или заводит ячейку
    с этим university_id в его регионе. university_id nullable ради ячеек,
    заведённых вручную до появления каталога (scripts/admin.py cell-add)."""

    __tablename__ = "university_cells"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    university_id: Mapped[int | None] = mapped_column(ForeignKey("universities.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    # Родительный падеж («СПбГУ» не склоняется, но «Урал» → «Урала» —
    # тот же приём, что и Region.genitive_name).
    genitive_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leader_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    vk_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Выделено регионом, копейки. Факт считается суммой Transaction.cell_id
    # (utils/balance_calc.get_cell_totals) — ровно как план/факт мероприятия.
    allocated_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    region: Mapped["Region"] = relationship(back_populates="cells")
    university: Mapped["University | None"] = relationship()
    leader: Mapped["User | None"] = relationship(foreign_keys=[leader_user_id])


class CoordinatorRegion(Base):
    """Назначение региона координатору. Один регион — один координатор (ТЗ §3),
    что и закрепляет UNIQUE на region_id."""

    __tablename__ = "coordinator_regions"
    __table_args__ = (UniqueConstraint("region_id", name="uq_coordinator_regions_region"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    coordinator_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"))

    coordinator: Mapped["User"] = relationship(back_populates="coordinated_regions")
    region: Mapped["Region"] = relationship()


class Member(Base):
    """Человек в составе регионального отделения. Не пользователь системы:
    своего telegram_id и входа в кабинет у него в волне 1 нет (ТЗ §18)."""

    __tablename__ = "members"
    __table_args__ = (CheckConstraint("stars >= 0", name="ck_members_stars_nonnegative"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    # None — «региональный» человек, не привязан к конкретному вузу
    # (регионы без ячеек, либо человек не относится ни к одной ячейке).
    cell_id: Mapped[int | None] = mapped_column(ForeignKey("university_cells.id"), nullable=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Формат "@username" — свободный текст, не связан с User.telegram_id
    # (тот числовой и приходит только через саморегистрацию); ник вводится
    # руками при заполнении/регистрации карточки, для внешней связи.
    telegram_username: Mapped[str | None] = mapped_column(String(33), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=MEMBER_STATUS_ACTIVIST)
    birth_date: Mapped[date_ | None] = mapped_column(Date, nullable=True)
    # Три вехи членства — не путать с датой создания записи (comment):
    # человека можно завести в системе позже, чем он реально пришёл, и не
    # каждая веха у него уже случилась (активист может не иметь ни
    # посвящения, ни выпуска). Обязательность see api/routers/members.py —
    # руководитель обязан заполнить веху, соответствующую статусу, который
    # он присваивает (посвящение — для «член Братства», выпуск — для
    # «выпускник»); более ранние вехи можно дозаполнить когда угодно.
    activist_joined_at: Mapped[date_ | None] = mapped_column(Date, nullable=True)
    member_inducted_at: Mapped[date_ | None] = mapped_column(Date, nullable=True)
    alumni_graduated_at: Mapped[date_ | None] = mapped_column(Date, nullable=True)
    comment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Свободный текст заменён на ссылку в каталог (scripts/migrate_university_catalog.py
    # переносит старые значения). None — человек ещё не учится/уже не учится.
    university_id: Mapped[int | None] = mapped_column(ForeignKey("universities.id"), nullable=True, index=True)
    faculty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Текущий курс (1-6); None, если ещё не поступил ИЛИ уже окончил вуз —
    # различает graduated_university ниже. Это чисто академический факт
    # (закончил учиться), не путать со статусом «Выпускник» (member.status —
    # решение руководителя о выходе из студенческого Братства, независимое).
    course: Mapped[int | None] = mapped_column(Integer, nullable=True)
    graduated_university: Mapped[bool] = mapped_column(Boolean, default=False)
    # Бакалавриат/магистратура/аспирантура/ординатура (EDUCATION_LEVEL_LABELS).
    education_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Длительность программы в годах. По ТЗ не вводится руками (всегда 4) —
    # поле остаётся ради bump_courses и на случай будущей необходимости.
    study_years: Mapped[int] = mapped_column(Integer, default=4)
    workplace: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Учебный год (см. services/education.py), за который курс уже повышен —
    # идемпотентность ежегодного автообновления 1 сентября.
    course_bumped_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # --- Геймификация: персонаж (план «Персонаж и инвентарь», MVP) ---------
    # Выбранный образ — один из готовых AI-сгенерированных образов
    # (api/routers/character.py::OUTFITS), не собирается из частей.
    # None — образ по умолчанию (первый в каталоге, всегда открыт).
    avatar_outfit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Свой круглый аватар — путь относительно STORAGE_DIR. Показывается в
    # ленте и комментариях; если не загружен, берётся образ из Академии.
    avatar_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Свободный рассказ о себе, который человек пишет сам. Показывается всем,
    # кто открыл его профиль, — в отличие от контактов ниже.
    about: Mapped[str | None] = mapped_column(String(600), nullable=True)
    # Показывать ли телефон в профиле остальным членам Братства. По умолчанию
    # нет: номер человек сдавал для учёта, а не для показа. Ник в телеграме
    # такого флага не имеет — написать друг другу можно всегда, ради этого
    # профиль и открывают (api/routers/profile.py::public_profile).
    show_phone: Mapped[bool] = mapped_column(Boolean, default=False)
    # Звёздочки — внутриигровая валюта за прогресс по ролям (api/routers/
    # character.py::BRANCHES), копится, тратится в будущем магазине скинов
    # и материальных наград. Не влияет на открытие базовых образов выше —
    # те по-прежнему считаются от общего числа затронутых заданий.
    stars: Mapped[int] = mapped_column(Integer, default=0)

    region: Mapped["Region"] = relationship(back_populates="members")
    university: Mapped["University | None"] = relationship()
    attendance: Mapped[list["EventAttendance"]] = relationship(
        back_populates="member", cascade="all, delete-orphan"
    )
    user: Mapped["User | None"] = relationship(
        back_populates="member", foreign_keys="User.member_id", uselist=False
    )
    quest_progress: Mapped[list["MemberQuestProgress"]] = relationship(
        back_populates="member", cascade="all, delete-orphan"
    )


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(16))  # income / expense
    emoji: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    region: Mapped["Region"] = relationship(back_populates="categories")
    keywords: Mapped[list["Keyword"]] = relationship(back_populates="category", cascade="all, delete-orphan")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="category")


class Keyword(Base):
    """Слово, по которому быстрый ввод («2500 канцелярия») находит категорию."""

    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    word: Mapped[str] = mapped_column(String(64), index=True)  # нормализованное, lowercase

    category: Mapped["Category"] = relationship(back_populates="keywords")


class Transaction(Base):
    """Финансовая операция. Баланс принадлежит региону, а не человеку (ТЗ §7)."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    # Необязательная привязка к мероприятию — заменяет копилки бота-донора:
    # план/факт бюджета считается суммой помеченных операций (ТЗ §8).
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True, index=True)
    # Необязательная привязка к вузовской ячейке — план/факт бюджета ячейки
    # считается так же, как у мероприятия (utils/balance_calc.get_cell_totals).
    cell_id: Mapped[int | None] = mapped_column(ForeignKey("university_cells.id"), nullable=True, index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    amount: Mapped[int] = mapped_column(Integer)  # копейки, всегда > 0
    type: Mapped[str] = mapped_column(String(16))  # income / expense
    date: Mapped[date_] = mapped_column(Date, index=True)
    comment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    region: Mapped["Region"] = relationship(back_populates="transactions")
    category: Mapped["Category | None"] = relationship(back_populates="transactions")
    event: Mapped["Event | None"] = relationship(back_populates="transactions")
    author: Mapped["User | None"] = relationship()


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    # None — мероприятие регионально-городское, не привязано к вузу.
    cell_id: Mapped[int | None] = mapped_column(ForeignKey("university_cells.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(128))
    date: Mapped[date_] = mapped_column(Date, index=True)
    # Необязательное — старые мероприятия и разовые встречи «на весь день»
    # его не имеют; без него напоминание (см. reminder_sent_on ниже) шлётся
    # от полуночи начала дня мероприятия.
    time: Mapped[time_ | None] = mapped_column(Time, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ответственный — человек из состава региона (Member), а не пользователь системы:
    # в волне 1 организаторы мероприятий кабинета не имеют (ТЗ §18).
    responsible_member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=EVENT_STATUS_PLANNED)
    planned_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)  # копейки
    # Повторяющееся событие: шаблон помечен is_recurring, порождённые записи
    # ссылаются на него через recurrence_parent_id (см. services/recurring_events.py).
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    recurrence_rule: Mapped[str | None] = mapped_column(String(16), nullable=True)  # weekly / biweekly / monthly
    recurrence_until: Mapped[date_ | None] = mapped_column(Date, nullable=True)
    recurrence_parent_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Напоминание за 24 часа тем, кто отметился «иду» (services/notifier.py
    # ::check_event_reminders) — дата отправки как идемпотентность (тот же
    # приём, что и Task.deadline_notified_on); момент отправки внутри дня
    # решает date+time минус 24 часа, не сама эта колонка.
    reminder_sent_on: Mapped[date_ | None] = mapped_column(Date, nullable=True)

    region: Mapped["Region"] = relationship(back_populates="events")
    responsible: Mapped["Member | None"] = relationship()
    attendance: Mapped[list["EventAttendance"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="event")
    documents: Mapped[list["Document"]] = relationship(back_populates="event")
    tasks: Mapped[list["EventTask"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="EventTask.position"
    )


class EventTask(Base):
    """Задача цели-мероприятия (раньше «подзадача») — своя обязательная дата
    и статус, несколько исполнителей через EventTaskAssignee (план из
    обсуждения: конференция как цель, «договориться с вузом»/«найти
    лекторов»/... как задачи).

    branch/stars — та же категория, что в «Академии» (api/routers/character.py
    ::BRANCHES), и награда звёздами за выполнение: руководитель выбирает их
    при постановке задачи, исполнитель видит задачу у себя в Академии, в
    Заданиях, в этой категории (api/routers/character.py::_character_payload).
    Начисление — не автоматическое: каждый исполнитель сам нажимает
    «Получить» (EventTaskAssignee.stars_claimed, api/routers/character.py
    ::claim_event_task_stars), как и в каталожных заданиях."""

    __tablename__ = "event_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    due_date: Mapped[date_] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default=EVENT_STATUS_PLANNED)
    position: Mapped[int] = mapped_column(Integer, default=0)
    branch: Mapped[str] = mapped_column(String(32), default="organizer")
    # Награда за задачу мероприятия отменена: рядовая работа отмечается
    # сделанной, а не оплачивается (api/routers/event_tasks.py). Колонка
    # оставлена ради истории — в ней лежит то, что было начислено раньше;
    # ничего нового в неё не пишется и наружу она не отдаётся.
    stars: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    event: Mapped["Event"] = relationship(back_populates="tasks")
    assignees: Mapped[list["EventTaskAssignee"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class EventTaskAssignee(Base):
    __tablename__ = "event_task_assignees"
    __table_args__ = (UniqueConstraint("task_id", "member_id", name="uq_event_task_assignee"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("event_tasks.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    # Красный кружок-уведомление в Академии — снимается, когда исполнитель
    # открывает свои Задания (api/routers/character.py::get_character).
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    # Звёзды за задачу берутся кнопкой «Получить» — не автоматически при
    # закрытии задачи (api/routers/character.py::claim_event_task_stars).
    stars_claimed: Mapped[bool] = mapped_column(Boolean, default=False)

    task: Mapped["EventTask"] = relationship(back_populates="assignees")
    member: Mapped["Member"] = relationship()


class EventAttendance(Base):
    __tablename__ = "event_attendance"
    __table_args__ = (UniqueConstraint("event_id", "member_id", name="uq_event_attendance"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    attended: Mapped[bool] = mapped_column(Boolean, default=True)

    event: Mapped["Event"] = relationship(back_populates="attendance")
    member: Mapped["Member"] = relationship(back_populates="attendance")


class Document(Base):
    """Файл хранится на диске сервера (STORAGE_DIR), в БД — только метаданные
    и относительный путь. Telegram file_id намеренно не используем (ТЗ §9)."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))  # относительно STORAGE_DIR
    original_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    doc_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # устав / протокол / шаблон / материалы
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    region: Mapped["Region"] = relationship(back_populates="documents")
    event: Mapped["Event | None"] = relationship(back_populates="documents")
    author: Mapped["User | None"] = relationship()


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline: Mapped[date_ | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=TASK_STATUS_NEW)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Дата, за которую исполнителю уже отправлено напоминание о приближении
    # дедлайна — чтобы не слать его повторно при каждом цикле нотификатора.
    deadline_notified_on: Mapped[date_ | None] = mapped_column(Date, nullable=True)
    overdue_notified: Mapped[bool] = mapped_column(Boolean, default=False)

    author: Mapped["User"] = relationship(foreign_keys=[from_user_id])
    assignee: Mapped["User"] = relationship(foreign_keys=[to_user_id])


# --- Новости -------------------------------------------------------------
# Пришли на смену «Рассылке» (BroadcastMessage/BroadcastRecipient, удалена):
# та адресовалась поимённо руководителям регионов. Новость видят все, кто
# открыл кабинет, — разделения по уровням доступа нет. От чьего имени сказано,
# показывает подпись поста (NewsPost.byline).


class NewsPost(Base):
    """Новость. Видна всем; от чьего имени сказано — говорит byline.

    Подпись сохраняется в момент публикации, а не считается при чтении: автор
    может сменить роль или уйти, но у вышедшего поста атрибуция должна
    остаться прежней (services/news.py::byline_for)."""

    __tablename__ = "news_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    # «Братство Академистов», «Академисты | Москва» либо имя участника.
    byline: Mapped[str] = mapped_column(String(128), default="")
    # Чем подписан пост: personal — человеком, bratstvo — от всего Братства,
    # otdelenie — от отделения или ячейки. Хранится, а не выводится из текста
    # подписи: по строке это гадание, а от вида зависят и аватар, и то,
    # открывается ли профиль (api/routers/news.py).
    byline_kind: Mapped[str] = mapped_column(String(16), default="personal")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    author: Mapped["User"] = relationship()
    photos: Mapped[list["NewsPhoto"]] = relationship(
        back_populates="post", cascade="all, delete-orphan", order_by="NewsPhoto.sort_order"
    )
    comments: Mapped[list["NewsComment"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )
    reactions: Mapped[list["NewsReaction"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )
    views: Mapped[list["NewsView"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )
    notifications: Mapped[list["NewsNotification"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )

class NewsPhoto(Base):
    """Фотография новости. Как и Document, файл лежит на диске (STORAGE_DIR),
    в БД — метаданные и относительный путь; Telegram file_id не используем."""

    __tablename__ = "news_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("news_posts.id"), index=True)
    stored_path: Mapped[str] = mapped_column(String(512))  # относительно STORAGE_DIR
    original_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # Порядок в галерее — тот, в котором автор выбрал файлы.
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    post: Mapped["NewsPost"] = relationship(back_populates="photos")


# Реакция одна — лайк. Набор из нескольких эмодзи пробовали, но в ленте он
# только дробил внимание; кортеж оставлен, чтобы хранилище и проверки не
# переписывать, если реакций снова станет больше.
NEWS_REACTIONS = ("❤️",)


class NewsReaction(Base):
    """Реакция на новость. На человека — одна: повторное нажатие снимает её,
    другая заменяет прежнюю (services/news.py::toggle_reaction)."""

    __tablename__ = "news_reactions"
    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_news_reaction_once"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("news_posts.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    emoji: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    post: Mapped["NewsPost"] = relationship(back_populates="reactions")


class NewsView(Base):
    """Факт просмотра новости человеком. Считаем уникальных читателей, а не
    открытия, поэтому пара (пост, человек) уникальна."""

    __tablename__ = "news_views"
    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_news_view_once"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("news_posts.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    post: Mapped["NewsPost"] = relationship(back_populates="views")


class ModerationWord(Base):
    """Слово из перечня проверки текстов (services/moderation.py).

    Лежит в базе, а не в коде: перечень будет меняться по жизни, и каждое
    изменение не должно требовать выкладки.

    Три вида. block — мат, публиковать нельзя. warn — спорное, пост выходит,
    но автор видит вопрос, а руководство получает уведомление. allow —
    исключение: слово, которое начинается так же, как корень из списка, но
    ругательством не является. Без исключений «мудрость» попадала бы под
    «муд», а «мандат» — под «манда».
    """

    __tablename__ = "moderation_words"
    __table_args__ = (UniqueConstraint("root", "kind", name="uq_moderation_word"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Корень в нормализованном виде: без регистра, ё сведена к е.
    root: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BureauMember(Base):
    """Человек в федеральном бюро и его должность там.

    Бюро — не отделение и не ячейка, а третий уровень: оно кураторствует
    регионы и ведёт то, что к региону не привязано. Поэтому членство в нём
    лежит отдельным измерением поверх состава: человек как числился в своём
    отделении, так и числится, а бюро добавляется к этому, не заменяя.

    Связь с аккаунтом, а не с карточкой в составе, — как у руководителя
    отделения (Region.leader_user_id) и координатора (CoordinatorRegion):
    бюро про то, чем человек управляет, а не где он состоит.

    Должность обязательна: список бюро должен отвечать на вопрос «кто за что»,
    а не только «кто там есть».
    """

    __tablename__ = "bureau_members"
    __table_args__ = (UniqueConstraint("user_id", name="uq_bureau_member_once"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    # Порядок в списке задаёт руководство: бюро — не алфавитный справочник,
    # первым идёт тот, кто его возглавляет.
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


class NewsNotification(Base):
    """Квитанция об отправленном уведомлении: какое сообщение в какой переписке
    появилось по этому посту.

    Нужна, чтобы уведомление можно было переписать, когда новость удалили.
    Telegram не знает наших постов — чтобы тронуть уже отправленное сообщение,
    ему нужен номер этого сообщения в конкретной переписке. Раньше номер
    выбрасывался сразу после отправки, и удалённая новость продолжала висеть
    в боте как ни в чём не бывало.

    Одна строка на каждого получателя: бот пишет не одно объявление на всех, а
    каждому лично, и сообщения это разные.
    """

    __tablename__ = "news_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("news_posts.id"), index=True)
    # Переписка с ботом: у личного чата совпадает с telegram_id человека.
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    post: Mapped["NewsPost"] = relationship(back_populates="notifications")


class NewsComment(Base):
    """Комментарий к новости. Пишет любой, кто новость видит; удаляет автор
    комментария, автор новости или федеральный/superuser (services/news.py)."""

    __tablename__ = "news_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("news_posts.id"), index=True)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    author: Mapped["User"] = relationship()
    post: Mapped["NewsPost"] = relationship(back_populates="comments")


class BirthdayNotice(Base):
    """Отметка «поздравление по этому человеку за этот год уже отправлено» —
    чтобы перезапуск бота в течение дня не слал уведомление второй раз."""

    __tablename__ = "birthday_notices"
    __table_args__ = (UniqueConstraint("member_id", "year", name="uq_birthday_notice"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    sent_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MembershipApplication(Base):
    """Заявка на вступление — публичная саморегистрация по многоразовой
    ссылке региона (Region.application_code). telegram_id известен сразу
    (заявитель уже написал боту), поэтому при одобрении отдельная
    ссылка-приглашение не нужна — User создаётся сразу активным
    (services/admin_actions.py, handlers/apply.py)."""

    __tablename__ = "membership_applications"
    __table_args__ = (
        Index(
            "uq_membership_applications_pending_telegram",
            "telegram_id",
            unique=True,
            sqlite_where=text("state = 'pending'"),
            postgresql_where=text("state = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    full_name: Mapped[str] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(String(33), nullable=True)
    birth_date: Mapped[date_ | None] = mapped_column(Date, nullable=True)
    university_id: Mapped[int | None] = mapped_column(ForeignKey("universities.id"), nullable=True)
    faculty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Только у заявок из собственной формы регистрации Mini App — внешняя
    # форма-отбор на academists.ru курс/уровень/статус не спрашивает
    # (см. api/routers/register.py).
    course: Mapped[int | None] = mapped_column(Integer, nullable=True)
    graduated_university: Mapped[bool] = mapped_column(Boolean, default=False)
    education_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    workplace: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Статус (активист/член Братства/выпускник) — человек выбирает сам при
    # регистрации (MEMBER_STATUS_LABELS), переносится на Member при одобрении.
    # Не путать с state ниже — то состояние самой заявки (на рассмотрении/
    # одобрена/отклонена), это разные вещи.
    member_status: Mapped[str] = mapped_column(String(16), default=MEMBER_STATUS_ACTIVIST)
    state: Mapped[str] = mapped_column(String(16), default=APPLICATION_STATE_PENDING)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    region: Mapped["Region"] = relationship()
    university: Mapped["University | None"] = relationship()
    reviewed_by: Mapped["User | None"] = relationship()


class Quest(Base):
    """Задание геймификации (план «Персонаж и инвентарь», MVP) — общий
    список на все регионы, заполняется вручную (пока без админ-интерфейса,
    правится через scripts/seed_quests.py). «Лесенка» повторов — простая
    CSV-строка порогов («1» — разовое, «1,3,10» — растущая цель на
    повторяемые дела вроде килы; см. thresholds_list ниже)."""

    __tablename__ = "quests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(128), unique=True)
    emoji: Mapped[str] = mapped_column(String(8))
    thresholds: Mapped[str] = mapped_column(String(64), default="1")
    # Награда за каждую ступень. Пустая строка — награда равна самому порогу
    # (лесенка «1,3,10» платит 1, 3 и 10), так было задумано изначально и так
    # осталось у всех прежних заданий. Но у «расклеить 500 стикеров» порог —
    # это объём работы, а не цена: платить 500 ★ за одну ступень значило бы
    # выдать за неё в семь раз больше, чем за весь остальной каталог. Поэтому
    # цену можно задать отдельно: «25,50,100,250,500» при «1,2,3,5,10».
    rewards: Mapped[str] = mapped_column(String(64), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def thresholds_list(self) -> list[int]:
        return [int(x) for x in self.thresholds.split(",") if x.strip()]

    def rewards_list(self) -> list[int]:
        """Цена каждой ступени. Без своей цены ступень стоит столько же,
        сколько её порог, — прежнее поведение для прежних заданий."""
        own = [int(x) for x in (self.rewards or "").split(",") if x.strip()]
        thresholds = self.thresholds_list()
        if not own:
            return thresholds
        # Короче лесенки — недостающие ступени стоят как их порог.
        return own + thresholds[len(own):]


class MemberQuestProgress(Base):
    """Счётчик выполнений одного задания одним человеком — сколько раз
    отмечено руководителем (api/routers/quests.py), не булево «сделано/нет»."""

    __tablename__ = "member_quest_progress"
    __table_args__ = (UniqueConstraint("member_id", "quest_id", name="uq_member_quest"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    quest_id: Mapped[int] = mapped_column(ForeignKey("quests.id"), index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    # Сколько звёзд уже забрано человеком по этому заданию (сумма пройденных
    # ступеней лесенки, за которые нажали «Получить») — см.
    # api/routers/character.py::claim_quest_stars. Руководитель, отмечая
    # «+1», прогресс двигает, но звёзды не начисляет — забирает сам человек.
    stars_claimed: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    member: Mapped["Member"] = relationship(back_populates="quest_progress")
    quest: Mapped["Quest"] = relationship()


class ShopPurchase(Base):
    """Покупка в магазине за звёзды (план «Персонаж и инвентарь») — каталог
    товаров захардкожен в api/routers/shop.py::SHOP_ITEMS (id/цена/тип), тут
    только факт покупки. kind='outfit' сразу даёт доступ к образу (см.
    api/routers/character.py); kind='physical' — материальная вещь, которую
    отдают очно, fulfilled выставляет руководитель после выдачи."""

    __tablename__ = "shop_purchases"
    __table_args__ = (UniqueConstraint("member_id", "item_id", name="uq_shop_purchase_member_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    item_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))
    price_stars: Mapped[int] = mapped_column(Integer)
    fulfilled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Красный кружок у руководителя на вкладке «Состав» — снимается, когда он
    # открывает покупки этого человека (api/routers/shop.py::list_member_purchases).
    seen_by_leader: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    member: Mapped["Member"] = relationship()
