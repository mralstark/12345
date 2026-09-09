"""CLI администратора: регионы, назначение управленческих ролей (ТЗ §3).

Управленческая роль (leader/cell_leader/coordinator/federal) назначается
только человеку, который уже сам зарегистрировался в боте по ссылке региона
(services/admin_actions.py::_resolve_or_promote) — найдите его id через
members-search, затем передайте --member-id. superuser — техническая роль
без «Состава», обычно через SUPERUSER_TELEGRAM_IDS в .env, но при
необходимости --telegram-id создаст запись напрямую.

    python -m scripts.admin regions
    python -m scripts.admin region-add "Москва" --genitive "Москвы"
    python -m scripts.admin cells --region "Москва"
    python -m scripts.admin cell-add --region "Москва" --name "МГИМО" --genitive "МГИМО"
    python -m scripts.admin members-search "Иванов" --region "Москва"
    python -m scripts.admin user-add --member-id 42 --role leader --region "Москва"
    python -m scripts.admin user-add --member-id 42 --role coordinator --regions "Москва,Тула"
    python -m scripts.admin user-add --member-id 42 --role cell_leader --region "Москва" --cell "МГИМО"
    python -m scripts.admin user-add --member-id 42 --role federal
    python -m scripts.admin user-add --name "Технический" --role superuser --telegram-id 123456789
    python -m scripts.admin users
    python -m scripts.admin user-deactivate --user-id 3
"""

import argparse
import asyncio
import sys

from sqlalchemy import delete, func, select

from config import BOT_PROXY_URL, BOT_TOKEN
from database.db import async_session, init_db
from database.models import (
    ROLE_CELL_LEADER,
    ROLE_COORDINATOR,
    ROLE_FEDERAL,
    ROLE_LABELS,
    ROLE_LEADER,
    ROLE_SUPERUSER,
    NewsPost,
    Category,
    CoordinatorRegion,
    Document,
    Event,
    EventAttendance,
    Keyword,
    Member,
    Region,
    Task,
    Transaction,
    UniversityCell,
    User,
)
from services.admin_actions import (
    create_cell_leader,
    create_coordinator,
    create_federal,
    create_leader,
    create_region,
)
from utils.invites import application_link

ROLES = (ROLE_SUPERUSER, ROLE_FEDERAL, ROLE_COORDINATOR, ROLE_LEADER, ROLE_CELL_LEADER)


async def _bot_username() -> str:
    """Имя бота для ссылки саморегистрации. Без токена — заглушка, чтобы CLI
    оставался работоспособным на машине без доступа к Telegram."""
    if not BOT_TOKEN:
        return "<имя_бота>"
    try:
        from aiogram import Bot
        from aiogram.client.session.aiohttp import AiohttpSession

        # Через тот же прокси, что и бот: иначе на машине без прямого доступа
        # к api.telegram.org CLI не сможет узнать имя бота для ссылки.
        session = AiohttpSession(proxy=BOT_PROXY_URL) if BOT_PROXY_URL else None
        bot = Bot(token=BOT_TOKEN, session=session)
        try:
            me = await bot.get_me()
            return me.username or "<имя_бота>"
        finally:
            await bot.session.close()
    except Exception as exc:  # noqa: BLE001 — сеть может быть недоступна, это не повод падать
        print(f"(не удалось получить имя бота: {exc})", file=sys.stderr)
        return "<имя_бота>"


async def cmd_regions() -> None:
    async with async_session() as session:
        result = await session.execute(select(Region).order_by(Region.name))
        regions = list(result.scalars().all())
        if not regions:
            print("Регионов пока нет.")
            return
        for region in regions:
            leader = await session.get(User, region.leader_user_id) if region.leader_user_id else None
            coord_row = await session.execute(
                select(User)
                .join(CoordinatorRegion, CoordinatorRegion.coordinator_user_id == User.id)
                .where(CoordinatorRegion.region_id == region.id)
            )
            coordinator = coord_row.scalar_one_or_none()
            flag = "" if region.is_active else " [архив]"
            print(
                f"#{region.id:<3} {region.name}{flag}\n"
                f"      родительный падеж: {region.genitive_name or '— (используется как есть)'}\n"
                f"      руководитель: {leader.full_name if leader else '—'}\n"
                f"      координатор:  {coordinator.full_name if coordinator else '—'}"
            )


async def cmd_region_add(name: str, genitive: str | None) -> None:
    async with async_session() as session:
        try:
            region = await create_region(session, name, genitive)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Регион «{region.name}» создан, id={region.id}")
        # Подпись давно собирается без падежа (utils/roles::region_display_name),
        # а подсказка обещала прежний формат и советовала --genitive.
        print(f'Руководителя будут подписывать так: «Руководитель — Академисты | {region.name}»')

    # У совсем нового региона руководителя ещё нет — только он сам может
    # потом получить эту ссылку через бота («Управление» -> «Ссылка
    # отделения»), поэтому печатаем её здесь: без неё некому даже
    # саморегистрироваться первым и стать руководителем.
    if region.application_code:
        username = await _bot_username()
        print(f"Ссылка саморегистрации (для самого первого человека/будущего руководителя):")
        print(f"  {application_link(username, region.application_code)}")


async def cmd_cells(region_name: str | None) -> None:
    async with async_session() as session:
        stmt = select(UniversityCell).order_by(UniversityCell.region_id, UniversityCell.name)
        if region_name:
            region_obj = (await session.execute(select(Region).where(Region.name == region_name))).scalar_one_or_none()
            if region_obj is None:
                print(f"Регион «{region_name}» не найден.")
                return
            stmt = stmt.where(UniversityCell.region_id == region_obj.id)

        cells = list((await session.execute(stmt)).scalars().all())
        if not cells:
            print("Ячеек пока нет.")
            return
        for cell in cells:
            region = await session.get(Region, cell.region_id)
            leader = await session.get(User, cell.leader_user_id) if cell.leader_user_id else None
            flag = "" if cell.is_active else " [архив]"
            print(
                f"#{cell.id:<3} {cell.name}{flag} — {region.name if region else '?'}\n"
                f"      руководитель: {leader.full_name if leader else '—'}\n"
                f"      VK: {cell.vk_url or '—'}"
            )


async def cmd_cell_add(region_name: str, name: str, genitive: str | None, vk_url: str | None) -> None:
    async with async_session() as session:
        region_obj = (await session.execute(select(Region).where(Region.name == region_name))).scalar_one_or_none()
        if region_obj is None:
            print(f"Регион «{region_name}» не найден — создайте его командой region-add.")
            return
        existing = await session.execute(
            select(UniversityCell).where(UniversityCell.region_id == region_obj.id, UniversityCell.name == name)
        )
        if existing.scalar_one_or_none():
            print(f"Ячейка «{name}» в регионе «{region_name}» уже существует.")
            return

        cell = UniversityCell(region_id=region_obj.id, name=name, genitive_name=genitive, vk_url=vk_url)
        session.add(cell)
        await session.commit()
        await session.refresh(cell)
        print(f"Ячейка «{cell.name}» ({region_name}) создана, id={cell.id}")
        print(f'Руководителя будут подписывать так: «Руководитель ячейки {cell.name}»')
        print(f"Дальше: user-add --member-id N --role cell_leader --region \"{region_name}\" --cell \"{name}\"")


async def cmd_users() -> None:
    async with async_session() as session:
        result = await session.execute(select(User).order_by(User.role, User.full_name))
        users = list(result.scalars().all())
        if not users:
            print("Пользователей пока нет.")
            return
        for user in users:
            status = "активен" if user.is_active else "отключён"
            bound = f"tg:{user.telegram_id}" if user.telegram_id else "не привязан"
            print(f"#{user.id:<3} {user.full_name:<28} {ROLE_LABELS.get(user.role, user.role):<34} {bound}, {status}")


async def cmd_user_add(
    name: str | None,
    role: str,
    region: str | None,
    regions: str | None,
    phone: str | None,
    cell: str | None = None,
    member_id: int | None = None,
    telegram_id: int | None = None,
) -> None:
    if role not in ROLES:
        print(f"Неизвестная роль: {role}. Допустимые: {', '.join(ROLES)}")
        return
    if role != ROLE_SUPERUSER and not member_id:
        print("Нужен --member-id N — человек уже должен быть саморегистрирован в боте")
        print("(найти его id: scripts.admin members-search «часть ФИО» --region ...).")
        return

    async with async_session() as session:
        try:
            if role == ROLE_SUPERUSER:
                # Техническая роль — без региона/ячейки/состава, обычно вместо
                # этого используют SUPERUSER_TELEGRAM_IDS (см. utils/users.py);
                # --telegram-id тут — явное исключение для отдельной записи.
                if not name:
                    print("Для роли superuser нужен --name «ФИО».")
                    return
                user = User(full_name=name, role=ROLE_SUPERUSER, phone=phone, telegram_id=telegram_id)
                session.add(user)
                await session.commit()
                await session.refresh(user)
                # Такой человек — единственный в системе без карточки в
                # «Составе», а значит и без личного кабинета. Терпимо только
                # на пустой системе, где отделений ещё нет; как только они
                # появятся, карточку надо завести, иначе он останется без
                # ленты, профиля и задач.
                print("Внимание: у этой записи нет карточки в «Составе» — личного кабинета")
                print("          не будет. Заведите его в «Составе» отделения и свяжите.")

            elif role == ROLE_LEADER:
                if not region:
                    print("Для роли leader нужен --region «Название региона».")
                    return
                region_obj = (await session.execute(select(Region).where(Region.name == region))).scalar_one_or_none()
                if region_obj is None:
                    print(f"Регион «{region}» не найден — создайте его командой region-add.")
                    return
                if region_obj.leader_user_id:
                    old = await session.get(User, region_obj.leader_user_id)
                    print(f"Внимание: у региона уже был руководитель ({old.full_name if old else '?'}) — заменяю.")
                user = await create_leader(session, region_obj.id, member_id=member_id, phone=phone)

            elif role == ROLE_CELL_LEADER:
                if not region or not cell:
                    print('Для роли cell_leader нужны --region "Регион" и --cell "Название ячейки".')
                    return
                region_obj = (await session.execute(select(Region).where(Region.name == region))).scalar_one_or_none()
                if region_obj is None:
                    print(f"Регион «{region}» не найден — создайте его командой region-add.")
                    return
                cell_obj = (
                    await session.execute(
                        select(UniversityCell).where(
                            UniversityCell.region_id == region_obj.id, UniversityCell.name == cell
                        )
                    )
                ).scalar_one_or_none()
                if cell_obj is None:
                    print(f"Ячейка «{cell}» в регионе «{region}» не найдена — создайте её командой cell-add.")
                    return
                if cell_obj.leader_user_id:
                    old = await session.get(User, cell_obj.leader_user_id)
                    print(f"Внимание: у ячейки уже был руководитель ({old.full_name if old else '?'}) — заменяю.")
                user = await create_cell_leader(session, cell_obj.id, member_id=member_id, phone=phone)

            elif role == ROLE_COORDINATOR:
                names = [n.strip() for n in (regions or region or "").split(",") if n.strip()]
                if not names:
                    print("Для роли coordinator нужен --regions «Регион1,Регион2».")
                    return
                region_ids = []
                for region_name in names:
                    region_obj = (
                        await session.execute(select(Region).where(Region.name == region_name))
                    ).scalar_one_or_none()
                    if region_obj is None:
                        print(f"Регион «{region_name}» не найден — пропускаю.")
                        continue
                    region_ids.append(region_obj.id)
                user = await create_coordinator(session, region_ids, member_id=member_id, phone=phone)

            elif role == ROLE_FEDERAL:
                user = await create_federal(session, member_id=member_id, phone=phone)

            else:
                print(f"Роль {role} не поддерживается этой командой.")
                return
        except ValueError as exc:
            print(str(exc))
            return

    print(f"Готово: #{user.id} {user.full_name} — {ROLE_LABELS[role]}")


async def cmd_members_search(region_name: str | None, query: str) -> None:
    async with async_session() as session:
        stmt = select(Member).where(Member.is_active.is_(True))
        if region_name:
            region_obj = (await session.execute(select(Region).where(Region.name == region_name))).scalar_one_or_none()
            if region_obj is None:
                print(f"Регион «{region_name}» не найден.")
                return
            stmt = stmt.where(Member.region_id == region_obj.id)
        if query:
            stmt = stmt.where(Member.full_name.ilike(f"%{query}%"))
        members = list((await session.execute(stmt.order_by(Member.full_name).limit(30))).scalars().all())
        if not members:
            print("Никого не найдено.")
            return
        for member in members:
            has_account = (
                await session.execute(select(User.id).where(User.member_id == member.id))
            ).scalar_one_or_none()
            flag = " [уже есть аккаунт]" if has_account else ""
            print(f"#{member.id:<4} {member.full_name}{flag}")


async def cmd_user_deactivate(user_id: int) -> None:
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            print(f"Пользователь #{user_id} не найден.")
            return
        user.is_active = False
        await session.commit()
        print(f"Пользователь #{user_id} ({user.full_name}) отключён.")


async def cmd_region_purge(region_id: int, confirm: bool) -> None:
    """Физическое удаление региона со всеми зависимыми записями. Только CLI,
    только для уже архивированного региона — двойной барьер против случайности
    (сначала region-archive, отдельно и осознанно — потом purge)."""
    async with async_session() as session:
        region = await session.get(Region, region_id)
        if region is None:
            print(f"Регион #{region_id} не найден.")
            return
        if region.is_active:
            print(f"Регион «{region.name}» ещё активен — сначала архивируйте его (кабинет, «Управление» -> «Архивировать»).")
            return

        counts = {
            "состав": await session.scalar(select(func.count()).select_from(Member).where(Member.region_id == region_id)),
            "операции": await session.scalar(select(func.count()).select_from(Transaction).where(Transaction.region_id == region_id)),
            "мероприятия": await session.scalar(select(func.count()).select_from(Event).where(Event.region_id == region_id)),
            "документы": await session.scalar(select(func.count()).select_from(Document).where(Document.region_id == region_id)),
            "ячейки": await session.scalar(select(func.count()).select_from(UniversityCell).where(UniversityCell.region_id == region_id)),
        }
        print(f"Регион «{region.name}» и связанные записи будут удалены безвозвратно:")
        for label, n in counts.items():
            print(f"  {label}: {n}")
        leader_warn = " (аккаунт руководителя останется, но потеряет привязку — проверьте отдельно)" if region.leader_user_id else ""
        if leader_warn:
            print(f"  внимание: у региона есть руководитель{leader_warn}")

        if not confirm:
            print("\nНичего не удалено. Повторите с флагом --confirm, если это точно нужно.")
            return

        event_ids = select(Event.id).where(Event.region_id == region_id)
        category_ids = select(Category.id).where(Category.region_id == region_id)
        await session.execute(delete(EventAttendance).where(EventAttendance.event_id.in_(event_ids)))
        await session.execute(delete(Keyword).where(Keyword.category_id.in_(category_ids)))
        await session.execute(delete(Document).where(Document.region_id == region_id))
        await session.execute(delete(Transaction).where(Transaction.region_id == region_id))
        await session.execute(delete(Event).where(Event.region_id == region_id))
        await session.execute(delete(Category).where(Category.region_id == region_id))
        await session.execute(delete(Member).where(Member.region_id == region_id))
        await session.execute(delete(UniversityCell).where(UniversityCell.region_id == region_id))
        await session.execute(delete(CoordinatorRegion).where(CoordinatorRegion.region_id == region_id))
        await session.delete(region)
        await session.commit()
        print(f"\nРегион «{region.name}» удалён безвозвратно.")


async def cmd_user_purge(user_id: int, confirm: bool) -> None:
    """Физическое удаление пользователя — только если у него нет вообще
    никакой истории (задач, операций и т.п.): удалять/обнулять чужие
    финансовые записи ради чистки одного тестового аккаунта неправильно,
    поэтому при малейшей зависимости команда просто отказывает."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            print(f"Пользователь #{user_id} не найден.")
            return
        if user.is_active:
            print(f"«{user.full_name}» ещё активен — сначала деактивируйте (user-deactivate или «Управление» в боте).")
            return

        blockers = {
            "задачи": await session.scalar(
                select(func.count()).select_from(Task).where((Task.from_user_id == user_id) | (Task.to_user_id == user_id))
            ),
            "новости (автор)": await session.scalar(
                select(func.count()).select_from(NewsPost).where(NewsPost.author_user_id == user_id)
            ),
            "операции (автор)": await session.scalar(
                select(func.count()).select_from(Transaction).where(Transaction.author_id == user_id)
            ),
            "документы (автор)": await session.scalar(
                select(func.count()).select_from(Document).where(Document.author_id == user_id)
            ),
            "координация регионов": await session.scalar(
                select(func.count()).select_from(CoordinatorRegion).where(CoordinatorRegion.coordinator_user_id == user_id)
            ),
            "руководит регионом": await session.scalar(
                select(func.count()).select_from(Region).where(Region.leader_user_id == user_id)
            ),
            "руководит ячейкой": await session.scalar(
                select(func.count()).select_from(UniversityCell).where(UniversityCell.leader_user_id == user_id)
            ),
        }
        active_blockers = {k: v for k, v in blockers.items() if v}
        if active_blockers:
            print(f"«{user.full_name}» нельзя удалить — есть связанные записи:")
            for label, n in active_blockers.items():
                print(f"  {label}: {n}")
            print("Это не пустая тестовая запись — физическое удаление отключено намеренно.")
            return

        if not confirm:
            print(f"«{user.full_name}» можно удалить безопасно (зависимостей нет). Повторите с --confirm.")
            return

        await session.delete(user)
        await session.commit()
        print(f"Пользователь «{user.full_name}» удалён безвозвратно.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Администрирование кабинета Братства Академистов")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("regions", help="список регионов")
    sub.add_parser("users", help="список пользователей")

    p = sub.add_parser("region-add", help="добавить регион")
    p.add_argument("name")
    p.add_argument(
        "--genitive",
        help='название в родительном падеже для подписи руководителя, например "Красноярска" для региона "Красноярск"',
    )

    sub.add_parser("cells", help="список вузовских ячеек").add_argument(
        "--region", help="ограничить список одним регионом"
    )

    p = sub.add_parser("cell-add", help="добавить вузовскую ячейку в регионе")
    p.add_argument("--region", required=True)
    p.add_argument("--name", required=True)
    p.add_argument(
        "--genitive",
        help='название в родительном падеже для подписи руководителя, например "МГИМО" (не склоняется) или "Урала"',
    )
    p.add_argument("--vk-url", help="ссылка на паблик ВКонтакте")

    p = sub.add_parser("user-add", help="назначить роль уже саморегистрированному человеку из состава")
    p.add_argument(
        "--member-id",
        type=int,
        help="существующий человек из «Состава», уже прошедший саморегистрацию (см. members-search); "
        "обязателен для всех ролей, кроме superuser",
    )
    p.add_argument("--name", help="ФИО — нужен только для роли superuser (техническая роль, не привязана к «Составу»)")
    p.add_argument(
        "--telegram-id",
        type=int,
        help="только для роли superuser — telegram_id напрямую, если не используете SUPERUSER_TELEGRAM_IDS",
    )
    p.add_argument("--role", required=True, choices=ROLES)
    p.add_argument("--region", help="регион для ролей leader/cell_leader")
    p.add_argument("--regions", help="регионы через запятую для роли coordinator")
    p.add_argument("--cell", help="название ячейки для роли cell_leader")
    p.add_argument("--phone")

    p = sub.add_parser("members-search", help="найти человека в составе — для --member-id в user-add")
    p.add_argument("query", help="часть ФИО")
    p.add_argument("--region", help="ограничить регионом")

    p = sub.add_parser("user-deactivate", help="отключить пользователя")
    p.add_argument("--user-id", type=int, required=True)

    p = sub.add_parser(
        "region-purge",
        help="физически удалить архивированный регион со всеми записями (необратимо)",
    )
    p.add_argument("--id", type=int, required=True, dest="region_id")
    p.add_argument("--confirm", action="store_true", help="без флага только покажет, что будет удалено")

    p = sub.add_parser(
        "user-purge",
        help="физически удалить деактивированного пользователя без истории (необратимо)",
    )
    p.add_argument("--id", type=int, required=True, dest="user_id")
    p.add_argument("--confirm", action="store_true", help="без флага только проверит, можно ли удалить")

    return parser


async def main() -> None:
    args = build_parser().parse_args()
    await init_db()

    if args.command == "regions":
        await cmd_regions()
    elif args.command == "region-add":
        await cmd_region_add(args.name, args.genitive)
    elif args.command == "cells":
        await cmd_cells(args.region)
    elif args.command == "cell-add":
        await cmd_cell_add(args.region, args.name, args.genitive, args.vk_url)
    elif args.command == "users":
        await cmd_users()
    elif args.command == "user-add":
        await cmd_user_add(
            args.name, args.role, args.region, args.regions, args.phone, args.cell, args.member_id, args.telegram_id
        )
    elif args.command == "members-search":
        await cmd_members_search(args.region, args.query)
    elif args.command == "user-deactivate":
        await cmd_user_deactivate(args.user_id)
    elif args.command == "region-purge":
        await cmd_region_purge(args.region_id, args.confirm)
    elif args.command == "user-purge":
        await cmd_user_purge(args.user_id, args.confirm)


if __name__ == "__main__":
    asyncio.run(main())
