"""Contact confirmation uses the real Telegram actor, never a viewed account."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from database.models import Member, TelegramPhoneVerification, User
from handlers.start import request_verified_phone, save_verified_phone
from utils.users import start_impersonation, stop_impersonation


def contact_message(telegram_id, *, phone="+79991112233", contact_id=None, chat="private"):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=telegram_id, full_name="Тестовый участник"),
        chat=SimpleNamespace(type=chat),
        contact=SimpleNamespace(
            user_id=telegram_id if contact_id is None else contact_id,
            phone_number=phone,
        ),
        answer=AsyncMock(),
    )


async def add_contact_card(session, user, region_id, phone):
    member = Member(region_id=region_id, full_name=user.full_name, phone=phone)
    session.add(member)
    await session.flush()
    user.member_id = member.id
    user.phone = phone
    await session.commit()
    return member


@pytest.mark.parametrize("previous_verification", [False, True])
async def test_contact_cannot_mutate_either_account_while_impersonating(session, world, previous_verification):
    actor, target = world["superuser"], world["leader_moscow"]
    actor_member = await add_contact_card(session, actor, world["moscow"].id, "+79000000001")
    target_member = await add_contact_card(session, target, world["moscow"].id, "+79000000002")
    if previous_verification:
        session.add(TelegramPhoneVerification(telegram_id=actor.telegram_id, phone=actor.phone))
        await session.commit()
    await start_impersonation(session, actor, target.id)

    message = contact_message(actor.telegram_id)
    await request_verified_phone(message)
    assert "Войти как" in message.answer.call_args.args[0]
    assert not hasattr(message.answer.call_args.kwargs.get("reply_markup"), "keyboard")
    await save_verified_phone(message)
    for row in (actor, target, actor_member, target_member):
        await session.refresh(row)
    assert actor.phone == actor_member.phone == "+79000000001"
    assert target.phone == target_member.phone == "+79000000002"
    verification = await session.get(TelegramPhoneVerification, actor.telegram_id)
    assert (verification.phone if verification else None) == (actor.phone if previous_verification else None)
    assert await session.get(TelegramPhoneVerification, target.telegram_id) is None
    assert actor.view_as_user_id == target.id
    assert "Войти как" in message.answer.call_args.args[0]

    # Exiting view-as restores ordinary own-account confirmation.
    await stop_impersonation(session, actor)
    await save_verified_phone(contact_message(actor.telegram_id))
    for row in (actor, target, actor_member, target_member):
        await session.refresh(row)
    assert actor.phone == actor_member.phone == "+79991112233"
    assert target.phone == target_member.phone == "+79000000002"


async def test_contact_updates_registered_actor_and_own_member(session, world):
    actor = world["leader_moscow"]
    member = await add_contact_card(session, actor, world["moscow"].id, "+79000000001")
    message = contact_message(actor.telegram_id, phone="+7 999 111-22-33")
    await save_verified_phone(message)
    await session.refresh(actor)
    await session.refresh(member)
    assert actor.phone == member.phone == "+79991112233"
    assert (await session.get(TelegramPhoneVerification, actor.telegram_id)).phone == actor.phone
    assert "Номер подтверждён" in message.answer.call_args.args[0]


async def test_unknown_contact_can_confirm_phone_without_creating_user(session):
    from sqlalchemy import select

    await save_verified_phone(contact_message(999510))
    assert (await session.get(TelegramPhoneVerification, 999510)).phone == "+79991112233"
    assert (await session.execute(select(User).where(User.telegram_id == 999510))).scalar_one_or_none() is None


@pytest.mark.parametrize("chat", ["group", "supergroup", "channel"])
async def test_contact_and_phone_prompt_require_private_chat(session, world, chat):
    actor = world["leader_moscow"]
    member = await add_contact_card(session, actor, world["moscow"].id, "+79000000001")
    for handler in (request_verified_phone, save_verified_phone):
        message = contact_message(actor.telegram_id, chat=chat)
        await handler(message)
        assert "личном чате" in message.answer.call_args.args[0]
        assert not hasattr(message.answer.call_args.kwargs.get("reply_markup"), "keyboard")
    await session.refresh(actor)
    await session.refresh(member)
    assert actor.phone == member.phone == "+79000000001"
    assert await session.get(TelegramPhoneVerification, actor.telegram_id) is None


async def test_disabled_actor_cannot_change_phone_in_handler(session, world):
    actor = world["leader_moscow"]
    member = await add_contact_card(session, actor, world["moscow"].id, "+79000000001")
    actor.is_active = False
    await session.commit()
    message = contact_message(actor.telegram_id)
    await request_verified_phone(message)
    assert "Доступ" in message.answer.call_args.args[0]
    await save_verified_phone(message)
    await session.refresh(actor)
    await session.refresh(member)
    assert actor.phone == member.phone == "+79000000001"
    assert await session.get(TelegramPhoneVerification, actor.telegram_id) is None
    assert "Доступ" in message.answer.call_args.args[0]


@pytest.mark.parametrize("phone,contact_id", [("+79991112233", 888111), ("abc", None)])
async def test_foreign_or_invalid_contact_does_not_create_verification(session, phone, contact_id):
    message = contact_message(999511, phone=phone, contact_id=contact_id)
    await save_verified_phone(message)
    assert await session.get(TelegramPhoneVerification, 999511) is None
