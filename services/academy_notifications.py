"""Personal notices describe accessible new items, not unfinished work."""
import hashlib
import json


def notice_token(member, quest, row, item):
    if row is None or not member.is_active:
        return None
    assignment = bool(member.status == "activist" and quest.is_active and row.assigned_by_user_id
                      and not row.pending_count and not item["completed"])
    reward = item["claimable_stars"] > 0
    if not assignment and not reward:
        return None
    value = {
        "quest": quest.id,
        "assignment": [str(row.assigned_at), row.assigned_by_user_id, row.assignment_note] if assignment else None,
        "reward": [row.count, item["claimable_stars"], row.stars_claimed, row.earned_stars_floor] if reward else None,
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def add_notice(member, quest, row, item):
    token = notice_token(member, quest, row, item)
    item["notice_token"] = token
    item["is_unread"] = bool(token and token != row.personal_seen_token)
    return item
