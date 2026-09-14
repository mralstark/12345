from types import SimpleNamespace

from database.models import ROLE_SUPERUSER
from handlers import common


async def test_main_menu_escapes_database_values(monkeypatch):
    async def no_regions(_session, _user):
        return []

    async def zero(_session, _user):
        return 0

    async def role(_session, _user):
        return "<главный>"

    monkeypatch.setattr(common, "accessible_regions", no_regions)
    monkeypatch.setattr(common, "new_tasks_count", zero)
    monkeypatch.setattr(common, "pending_applications_count", zero)
    monkeypatch.setattr(common, "role_label", role)

    user = SimpleNamespace(
        id=1,
        role=ROLE_SUPERUSER,
        full_name="Иван <script>alert(1)</script>",
        _impersonated_by=SimpleNamespace(full_name="Админ & Co"),
    )
    text = await common.menu_text(None, user)

    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "Админ &amp; Co" in text
    assert "&lt;главный&gt;" in text
