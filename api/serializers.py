"""Приведение моделей к JSON. Суммы отдаются в копейках (int) — форматирует клиент."""

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    EDUCATION_LEVEL_LABELS,
    EVENT_STATUS_LABELS,
    EVENT_TASK_STATUS_LABELS,
    MEMBER_STATUS_LABELS,
    TASK_STATUS_LABELS,
    Category,
    Document,
    Event,
    EventTask,
    Member,
    MembershipApplication,
    Region,
    Task,
    Transaction,
    User,
)
from services.tasks import effective_status
from utils.roles import role_label
from utils.permissions import role_codes
from utils.tz import iso_utc


async def user_brief(session: AsyncSession, user: User | None) -> dict | None:
    if user is None:
        return None
    return {
        "id": user.id,
        "full_name": user.full_name,
        "role": user.role,
        "roles": role_codes(user),
        "role_label": await role_label(session, user),
    }


def region_brief(region: Region) -> dict:
    return {"id": region.id, "name": region.name}


def member_dict(member: Member, university_name: str | None = None, has_unseen_purchases: bool = False) -> dict:
    return {
        "id": member.id,
        "has_unseen_purchases": has_unseen_purchases,
        "region_id": member.region_id,
        "cell_id": member.cell_id,
        "full_name": member.full_name,
        "phone": member.phone,
        "telegram_username": member.telegram_username,
        "status": member.status,
        "status_label": MEMBER_STATUS_LABELS.get(member.status, member.status),
        "birth_date": member.birth_date.isoformat() if member.birth_date else None,
        "activist_joined_at": member.activist_joined_at.isoformat() if member.activist_joined_at else None,
        "member_inducted_at": member.member_inducted_at.isoformat() if member.member_inducted_at else None,
        "alumni_graduated_at": member.alumni_graduated_at.isoformat() if member.alumni_graduated_at else None,
        "comment": member.comment,
        "university_id": member.university_id,
        "university_name": university_name,
        "faculty": member.faculty,
        "course": member.course,
        "graduated_university": member.graduated_university,
        "education_level": member.education_level,
        "education_level_label": EDUCATION_LEVEL_LABELS.get(member.education_level) if member.education_level else None,
        "study_years": member.study_years,
        "workplace": member.workplace,
        "created_at": iso_utc(member.created_at),
    }


def category_dict(category: Category, keywords: list[str] | None = None) -> dict:
    return {
        "id": category.id,
        "name": category.name,
        "type": category.type,
        "emoji": category.emoji,
        "keywords": keywords or [],
    }


def transaction_dict(
    transaction: Transaction,
    category_name: str | None = None,
    event_title: str | None = None,
    author_name: str | None = None,
) -> dict:
    return {
        "id": transaction.id,
        "region_id": transaction.region_id,
        "cell_id": transaction.cell_id,
        "amount": transaction.amount,
        "type": transaction.type,
        "date": transaction.date.isoformat(),
        "comment": transaction.comment,
        "category_id": transaction.category_id,
        "category_name": category_name,
        "event_id": transaction.event_id,
        "event_title": event_title,
        "author_name": author_name,
    }


def event_dict(
    event: Event,
    responsible_name: str | None = None,
    fact: int | None = None,
    region_name: str | None = None,
    cell_name: str | None = None,
    responsible_phone: str | None = None,
    responsible_telegram: str | None = None,
) -> dict:
    return {
        "id": event.id,
        "region_id": event.region_id,
        "cell_id": event.cell_id,
        "title": event.title,
        "date": event.date.isoformat(),
        "time": event.time.strftime("%H:%M") if event.time else None,
        "description": event.description,
        "status": event.status,
        "status_label": EVENT_STATUS_LABELS.get(event.status, event.status),
        "planned_budget": event.planned_budget,
        "fact_expense": fact,
        "responsible_member_id": event.responsible_member_id,
        "responsible_name": responsible_name,
        "responsible_phone": responsible_phone,
        "responsible_telegram": responsible_telegram,
        "region_name": region_name,
        "cell_name": cell_name,
        "is_recurring": event.is_recurring,
        "recurrence_rule": event.recurrence_rule,
        "recurrence_until": event.recurrence_until.isoformat() if event.recurrence_until else None,
        "recurrence_parent_id": event.recurrence_parent_id,
    }


def event_public_dict(
    event: Event,
    going: bool | None,
    region_name: str | None = None,
    cell_name: str | None = None,
    responsible_name: str | None = None,
    responsible_phone: str | None = None,
    responsible_telegram: str | None = None,
) -> dict:
    """Мероприятие глазами обычного участника (api/routers/events.py::list_my_events)
    — без бюджета, но с тем, кто организует и как связаться (то же самое,
    что нужно понадобиться и руководителю — см. event_dict)."""
    return {
        "id": event.id,
        "title": event.title,
        "date": event.date.isoformat(),
        "time": event.time.strftime("%H:%M") if event.time else None,
        "description": event.description,
        "status": event.status,
        "status_label": EVENT_STATUS_LABELS.get(event.status, event.status),
        "is_recurring": event.is_recurring,
        "going": going,
        "region_name": region_name,
        "cell_name": cell_name,
        "responsible_name": responsible_name,
        "responsible_phone": responsible_phone,
        "responsible_telegram": responsible_telegram,
    }


def event_task_dict(task: EventTask, assignee_member_id: int | None, assignee_name: str | None = None) -> dict:
    return {
        "id": task.id,
        "event_id": task.event_id,
        "title": task.title,
        "due_date": task.due_date.isoformat(),
        "status": task.status,
        "status_label": EVENT_TASK_STATUS_LABELS.get(task.status, task.status),
        "position": task.position,
        # Один исполнитель, он же ответственный: спрашивать не с кого другого.
        "assignee_member_id": assignee_member_id,
        "assignee_name": assignee_name,
    }


def document_dict(document: Document, author_name: str | None = None, event_title: str | None = None) -> dict:
    return {
        "id": document.id,
        "region_id": document.region_id,
        "event_id": document.event_id,
        "event_title": event_title,
        "title": document.title,
        "original_name": document.original_name,
        "content_type": document.content_type,
        "size_bytes": document.size_bytes,
        "doc_type": document.doc_type,
        "author_name": author_name,
        "created_at": iso_utc(document.created_at),
        "download_url": f"/api/documents/{document.id}/download",
    }


def task_dict(task: Task, author: User | None = None, assignee: User | None = None) -> dict:
    status = effective_status(task)
    return {
        "id": task.id,
        "title": task.title,
        "text": task.text,
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "status": task.status,
        "effective_status": status,
        "status_label": TASK_STATUS_LABELS.get(status, status),
        "is_read": task.is_read,
        "from_user_id": task.from_user_id,
        "to_user_id": task.to_user_id,
        "from_name": author.full_name if author else None,
        "to_name": assignee.full_name if assignee else None,
        "created_at": iso_utc(task.created_at),
        "submitted_at": iso_utc(task.submitted_at),
        "reviewed_at": iso_utc(task.reviewed_at),
        "review_note": task.review_note,
    }


def application_dict(
    application: MembershipApplication,
    region_name: str | None = None,
    university_name: str | None = None,
) -> dict:
    return {
        "id": application.id,
        "region_id": application.region_id,
        "region_name": region_name,
        "full_name": application.full_name,
        "phone": application.phone,
        "birth_date": application.birth_date.isoformat() if application.birth_date else None,
        "university_id": application.university_id,
        "university_name": university_name,
        "faculty": application.faculty,
        "course": application.course,
        "graduated_university": application.graduated_university,
        "education_level": application.education_level,
        "education_level_label": EDUCATION_LEVEL_LABELS.get(application.education_level) if application.education_level else None,
        "workplace": application.workplace,
        "status": application.member_status,
        "status_label": MEMBER_STATUS_LABELS.get(application.member_status, application.member_status),
        "created_at": iso_utc(application.created_at),
    }
