"""Бизнес-логика решения по заявке на вступление — общая для бота
(handlers/apply.py) и тестов. Проверку прав (кто вправе рассматривать эту
заявку) сюда не кладём — это забота вызывающего, как и в
services/admin_actions.py (там тоже permission-check в хендлере, не в сервисе)."""

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    APPLICATION_STATE_APPROVED,
    APPLICATION_STATE_PENDING,
    APPLICATION_STATE_REJECTED,
    ROLE_PARTICIPANT,
    Member,
    MembershipApplication,
    User,
)
from services.education import academic_year
from utils.tz import now as tz_now
from utils.tz import today as tz_today
from utils.university_cells import resolve_member_cell


async def approve_application(session: AsyncSession, application_id: int, reviewer_id: int | None) -> User:
    """Создаёт Member + User сразу активным (telegram_id уже известен из
    заявки — отдельная ссылка-приглашение не нужна) и резолвит ячейку по
    вузу той же логикой, что и «Состав» (utils/university_cells.py).
    reviewer_id — None для автоматического одобрения без участия человека
    (api/routers/register.py — доверенные заранее известные люди, см.
    config.AUTO_FEDERAL_FULL_NAMES)."""
    application = await session.get(MembershipApplication, application_id)
    if application is None:
        raise ValueError("Заявка не найдена")
    if application.state != APPLICATION_STATE_PENDING:
        raise ValueError("Заявка уже рассмотрена")

    member = Member(
        region_id=application.region_id,
        full_name=application.full_name,
        phone=application.phone,
        telegram_username=application.telegram_username,
        # Статус человек выбрал сам при регистрации (не всегда «активист» —
        # решение изменено по просьбе, раньше руководитель выставлял статус
        # на своё усмотрение при одобрении).
        status=application.member_status,
        birth_date=application.birth_date,
        # Вехи (вступление/посвящение/выпуск) НЕ проставляются автоматически —
        # только руководитель вручную при редактировании карточки (план,
        # п.6б): дата одобрения анкеты — не то же самое, что дата реального
        # события, автоматика тут больше не угадывает.
        university_id=application.university_id,
        faculty=application.faculty,
        course=application.course,
        graduated_university=application.graduated_university,
        education_level=application.education_level,
        workplace=application.workplace,
        # Иначе ближайший цикл автообновления курса (services/education.py)
        # тут же «повысит» свежепринятого студента, не дожидаясь 1 сентября
        # (та же оговорка, что и в api/routers/members.py::create_member).
        course_bumped_year=academic_year(tz_today()) if application.course is not None else None,
    )
    session.add(member)
    await session.flush()

    # Принадлежность к ячейке выбирается отдельно от вуза. Для старых заявок
    # без cell_id сохраняем прежнее поведение как совместимый fallback.
    if application.cell_id is not None:
        member.cell_id = application.cell_id
    else:
        cell = await resolve_member_cell(session, application.region_id, application.university_id)
        member.cell_id = cell.id if cell is not None else None

    user = User(
        full_name=application.full_name,
        role=ROLE_PARTICIPANT,
        member_id=member.id,
        phone=application.phone,
        telegram_id=application.telegram_id,
    )
    session.add(user)

    application.state = APPLICATION_STATE_APPROVED
    application.reviewed_by_user_id = reviewer_id
    application.reviewed_at = tz_now().replace(tzinfo=None)

    await session.commit()
    await session.refresh(user)
    return user


async def reject_application(session: AsyncSession, application_id: int, reviewer_id: int) -> MembershipApplication:
    application = await session.get(MembershipApplication, application_id)
    if application is None:
        raise ValueError("Заявка не найдена")
    if application.state != APPLICATION_STATE_PENDING:
        raise ValueError("Заявка уже рассмотрена")

    application.state = APPLICATION_STATE_REJECTED
    application.reviewed_by_user_id = reviewer_id
    application.reviewed_at = tz_now().replace(tzinfo=None)
    await session.commit()
    await session.refresh(application)
    return application
