"""Auditable field inventory, shared by models, migration and storage checks."""
from database.encrypted import EncryptedValue

# Shared lookup domains are intentional: phone and Telegram joins cross tables.
LOOKUPS = {
    "telegram_id": ("identity.telegram", "int"),
    "chat_id": ("identity.telegram", "int"),
    "phone": ("identity.phone", "str"),
    "verified_phone": ("identity.phone", "str"),
    "subject": ("identity.vk-subject", "str"),
}
FIELDS = {
    "external_identities": "subject full_name verified_phone",
    "oauth_login_attempts": "code_verifier",
    "browser_sessions": "code",
    "telegram_phone_verifications": "telegram_id phone",
    "users": "telegram_id full_name phone",
    "members": "full_name phone telegram_username birth_date activist_joined_at member_inducted_at alumni_graduated_at comment faculty workplace about",
    "membership_applications": "telegram_id full_name phone telegram_username birth_date faculty workplace reject_reason",
    "transactions": "comment",
    "events": "title description",
    "event_tasks": "title",
    "documents": "title original_name",
    "tasks": "title text review_note",
    "news_posts": "text article_title article_summary article_content byline",
    "news_photos": "original_name",
    "news_notifications": "chat_id",
    "news_comments": "text",
    "member_quest_progress": "submitted_note assignment_note",
    "quest_activity": "note",
    "mentor_preparations": "payload",
}
DATE_FIELDS = {"birth_date", "activist_joined_at", "member_inducted_at", "alumni_graduated_at"}


def protected(table: str, field: str) -> EncryptedValue:
    if field not in FIELDS[table].split():
        raise ValueError("Field is not in the encryption inventory")
    if field in LOOKUPS:
        domain, kind = LOOKUPS[field]
        return EncryptedValue(domain, kind, deterministic=True)
    return EncryptedValue(table + "." + field, "date" if field in DATE_FIELDS else "str")
