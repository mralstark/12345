"""Личный кабинет: свои же данные из «Состава», доступные любому User с
привязанным member_id — независимо от управленческой роли (см. план
«Личный кабинет для каждого»). Статус и комментарий не самредактируемые —
статус это организационное решение, комментарий рабочая пометка руководителя."""

from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_db
from api.serializers import member_dict
from database.models import (
    MEMBER_STATUS_MEMBER,
    BureauMember,
    Member,
    NewsPhoto,
    NewsPost,
    NewsReaction,
    NewsView,
    Region,
    UniversityCell,
    University,
    User,
)
from services.education import academic_year
from services.moderation import check as check_text
from config import STORAGE_DIR
from services.news import avatar_token_valid, avatar_url_for_user, photo_url, save_avatar, short_name
from utils.parser import normalize_telegram_username
from utils.tz import iso_utc, today as tz_today
from utils.university_cells import resolve_member_cell

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfilePatch(BaseModel):
    phone: str | None = Field(default=None, max_length=32)
    telegram_username: str | None = Field(default=None, max_length=33)
    birth_date: date | None = None
    university_id: int | None = None
    faculty: str | None = Field(default=None, max_length=255)
    # Курс — единственное учебное поле, которое правит сам человек (план,
    # п.6а); статус/даты вех по-прежнему только руководитель, см. docstring
    # модуля выше.
    course: int | None = Field(default=None, ge=1, le=6)
    graduated_university: bool = False
    education_level: str | None = Field(default=None, max_length=32)
    workplace: str | None = Field(default=None, max_length=255)
    about: str | None = Field(default=None, max_length=600)
    # Показывать ли номер телефона остальным членам Братства в профиле.
    # Ник в телеграме такого выключателя не имеет: написать можно всем.
    show_phone: bool | None = None


async def _own_member(user: User, session: AsyncSession) -> Member:
    if user.member_id is None:
        raise HTTPException(404, "У этого аккаунта нет личного кабинета — он не привязан к «Составу»")
    member = await session.get(Member, user.member_id)
    if member is None:
        raise HTTPException(404, "Запись в составе не найдена")
    return member


def education_rows(
    university_name: str | None,
    cell_name: str | None,
    faculty: str | None,
    course: int | None,
    graduated: bool,
) -> list[dict]:
    """«Учёба» отдельными строками — и без того, что уже сказано выше.

    Строками, а не одной склейкой через точку: «Юридический факультет · 3 курс»
    читается как одно длинное значение, а это два разных факта, и на узком
    экране склейка переносится посреди слова.

    Ячейка у нас называется по вузу, а название ячейки стоит в шапке профиля
    рядом с регионом. Поэтому вуз, совпадающий с ячейкой, здесь не повторяем:
    он уже назван. Если человек учится не там, по чему названа его ячейка,
    вуз остаётся — там это уже не повтор, а новый факт.
    """
    rows = []
    if university_name and university_name != cell_name:
        rows.append({"label": "Вуз", "value": university_name})
    if faculty:
        rows.append({"label": "Факультет", "value": faculty})
    if graduated:
        rows.append({"label": "Учёба", "value": "окончил вуз"})
    elif course:
        rows.append({"label": "Курс", "value": f"{course}-й"})
    return rows


async def bureau_title(session: AsyncSession, user_id: int) -> str | None:
    """Должность в федеральном бюро — или None, если человек в нём не состоит.

    Видна всем, кто открыл профиль: сам список бюро закрыт от активистов, а
    принадлежность конкретного человека тайной не считается (решение
    руководства). То есть закрыт удобный список, а не факт.
    """
    row = (
        await session.execute(select(BureauMember).where(BureauMember.user_id == user_id))
    ).scalar_one_or_none()
    return row.title if row is not None else None


async def author_stats(session: AsyncSession, user_id: int) -> dict:
    """Сколько человек написал и сколько это прочитали. Счётчики про
    публикации и только про них: они и есть повод выложить следующий пост."""
    post_ids = list(
        (
            await session.execute(select(NewsPost.id).where(NewsPost.author_user_id == user_id))
        ).scalars().all()
    )
    if not post_ids:
        return {"posts": 0, "views": 0, "likes": 0}

    views = await session.scalar(
        select(func.count()).select_from(NewsView).where(NewsView.post_id.in_(post_ids))
    )
    likes = await session.scalar(
        select(func.count()).select_from(NewsReaction).where(NewsReaction.post_id.in_(post_ids))
    )
    return {"posts": len(post_ids), "views": views or 0, "likes": likes or 0}


async def author_posts(session: AsyncSession, user_id: int, limit: int = 20) -> list[dict]:
    """Публикации человека для его страницы — лентой карточек, той же формы,
    что и в общей ленте: фотографии, отметки «нравится», просмотры."""
    posts = list(
        (
            await session.execute(
                select(NewsPost)
                .where(NewsPost.author_user_id == user_id)
                .order_by(NewsPost.created_at.desc(), NewsPost.id.desc())
                .limit(limit)
            )
        ).scalars().all()
    )

    items = []
    for post in posts:
        photos = (
            await session.execute(
                select(NewsPhoto)
                .where(NewsPhoto.post_id == post.id)
                .order_by(NewsPhoto.sort_order, NewsPhoto.id)
            )
        ).scalars().all()
        items.append(
            {
                "id": post.id,
                "text": post.text,
                "byline": post.byline,
                "created_at": iso_utc(post.created_at),
                "photos": [{"id": photo.id, "url": photo_url(photo.id)} for photo in photos],
                "likes": await session.scalar(
                    select(func.count()).select_from(NewsReaction).where(NewsReaction.post_id == post.id)
                ) or 0,
                "views": await session.scalar(
                    select(func.count()).select_from(NewsView).where(NewsView.post_id == post.id)
                ) or 0,
            }
        )
    return items


@router.get("/me")
async def get_profile(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)
) -> dict:
    member = await _own_member(user, session)
    university = await session.get(University, member.university_id) if member.university_id else None
    region = await session.get(Region, member.region_id)
    cell = await session.get(UniversityCell, member.cell_id) if member.cell_id else None

    data = member_dict(member, university_name=university.name if university else None)
    data["region_name"] = region.name if region else None
    data["cell_name"] = cell.name if cell else None
    data["show_phone"] = member.show_phone
    data["about"] = member.about
    data["avatar"] = await avatar_url_for_user(session, user)
    data["is_member"] = member.status == MEMBER_STATUS_MEMBER
    data["role_title"] = await _role_title(session, user)
    data["bureau_title"] = await bureau_title(session, user.id)
    data["education"] = education_rows(
        university.name if university else None,
        cell.name if cell else None,
        member.faculty,
        member.course,
        member.graduated_university,
    )
    data["stats"] = await author_stats(session, user.id)
    data["posts"] = await author_posts(session, user.id)
    return data


@router.patch("/me")
async def update_profile(
    payload: ProfilePatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    member = await _own_member(user, session)
    data = payload.model_dump(exclude_unset=True)

    # «О себе» видит каждый, кто откроет профиль, — это такой же публичный
    # текст, как пост, и проверяется так же (services/moderation.py).
    if data.get("about"):
        await check_text(session, data["about"])

    if "telegram_username" in data:
        raw = data["telegram_username"]
        if not raw or not raw.strip():
            data["telegram_username"] = None
        else:
            try:
                data["telegram_username"] = normalize_telegram_username(raw)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

    if "university_id" in data:
        resolved_cell = await resolve_member_cell(session, member.region_id, data["university_id"])
        member.cell_id = resolved_cell.id if resolved_cell is not None else None

    if data.get("graduated_university"):
        data["course"] = None
        data["education_level"] = None
    elif "course" in data and data["course"] is not None:
        # Та же оговорка, что и в api/routers/members.py::create_member —
        # иначе ближайший цикл автообновления (services/education.py) тут же
        # «повысит» курс, не дожидаясь настоящего 1 сентября.
        member.course_bumped_year = academic_year(tz_today())

    for field, value in data.items():
        if isinstance(value, str):
            value = value.strip() or None
        setattr(member, field, value)

    await session.commit()
    await session.refresh(member)

    university = await session.get(University, member.university_id) if member.university_id else None
    return member_dict(member, university_name=university.name if university else None)


# --- Публичный профиль -------------------------------------------------------
# Открывается из подписи поста и из комментария: подпись без возможности
# узнать, кто это, — полдела. Ник в телеграме отдаём всегда — написать друг
# другу можно всем, ради этого профиль и открывают. Номер телефона — только
# с разрешения (Member.show_phone): его сдавали для учёта, а не для
# публикации. Дату рождения не показываем никому.

@router.post("/me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    member = await _own_member(user, session)
    await save_avatar(session, member, file)
    return {"avatar": await avatar_url_for_user(session, user)}


@router.delete("/me/avatar")
async def drop_avatar(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    member = await _own_member(user, session)
    if member.avatar_path:
        old = (STORAGE_DIR / member.avatar_path).resolve()
        if old.is_file() and STORAGE_DIR.resolve() in old.parents:
            old.unlink()
        member.avatar_path = None
        await session.commit()
    return {"avatar": await avatar_url_for_user(session, user)}


@router.get("/avatar/{member_id}")
async def avatar(
    member_id: int,
    t: str = "",
    session: AsyncSession = Depends(get_db),
) -> FileResponse:
    """По подписи из ссылки, а не по заголовку: запрос делает тег <img>
    (см. services/news.py::photo_token — там та же причина)."""
    if not avatar_token_valid(member_id, t):
        raise HTTPException(403, "Ссылка на аватар недействительна или устарела")
    member = await session.get(Member, member_id)
    if member is None or not member.avatar_path:
        raise HTTPException(404, "Аватар не найден")
    path = (STORAGE_DIR / member.avatar_path).resolve()
    if not path.is_file() or STORAGE_DIR.resolve() not in path.parents:
        raise HTTPException(404, "Файл не найден")
    return FileResponse(
        path,
        headers={"Cache-Control": "private, max-age=900", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{user_id}")
async def public_profile(
    user_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    target = await session.get(User, user_id)
    if target is None or target.member_id is None:
        raise HTTPException(404, "Профиль не найден")
    member = await session.get(Member, target.member_id)
    if member is None:
        raise HTTPException(404, "Профиль не найден")

    region = await session.get(Region, member.region_id)
    cell = await session.get(UniversityCell, member.cell_id) if member.cell_id else None
    university = await session.get(University, member.university_id) if member.university_id else None

    return {
        "user_id": user_id,
        "name": short_name(member.full_name),
        "status_label": member_dict(member).get("status_label"),
        # Герб рядом с именем — знак посвящения, а не должности: его носит
        # только член Братства, у активиста и выпускника его нет.
        "is_member": member.status == MEMBER_STATUS_MEMBER,
        "role_title": await _role_title(session, target),
        "bureau_title": await bureau_title(session, user_id),
        "about": member.about,
        "region": region.name if region is not None else None,
        "cell": cell.name if cell is not None else None,
        "education": education_rows(
            university.name if university is not None else None,
            cell.name if cell is not None else None,
            member.faculty,
            member.course,
            member.graduated_university,
        ),
        "avatar": await avatar_url_for_user(session, target),
        # Ник отдаём всегда: по нему открывается переписка, и ради этого
        # профиль чаще всего и открывают. Пустой — значит человека завели
        # через «Состав» и ник у него не спросили; кнопка это честно скажет.
        "telegram": member.telegram_username,
        # Номер — только с его разрешения: его сдавали для учёта. Скрытый не
        # отдаём вовсе, чтобы на экране не было даже строки «скрыто».
        "phone": member.phone if member.show_phone else None,
        "is_me": target.id == user.id,
        "stats": await author_stats(session, user_id),
        "posts": await author_posts(session, user_id),
    }


async def _role_title(session: AsyncSession, user: User) -> str | None:
    """Должность для профиля: членство показывает герб, а руководство — строка.
    У участника её нет вовсе."""
    from database.models import ROLE_CELL_LEADER, ROLE_COORDINATOR, ROLE_FEDERAL, ROLE_LEADER
    from utils.access import accessible_region_ids, actor_cell

    if user.role == ROLE_FEDERAL:
        return "Федеральный координатор"
    if user.role == ROLE_COORDINATOR:
        return "Координатор регионов"
    if user.role == ROLE_CELL_LEADER:
        cell = await actor_cell(session, user)
        return f"Руководитель ячейки{' ' + cell.name if cell is not None else ''}"
    if user.role == ROLE_LEADER:
        region_ids = await accessible_region_ids(session, user)
        region = await session.get(Region, region_ids[0]) if region_ids else None
        return f"Руководитель отделения{' ' + region.name if region is not None else ''}"
    return None
