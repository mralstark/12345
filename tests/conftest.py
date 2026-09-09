"""Общая обвязка тестов.

Переменные окружения выставляются до импорта приложения: database/db.py создаёт
движок на уровне модуля, и боевая БД в тестах не должна открываться ни разу.
"""

import os
import tempfile
from pathlib import Path

TEST_DB = Path(tempfile.gettempdir()) / "bratstvo_test.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN-FOR-TESTS"
os.environ["STORAGE_DIR"] = str(Path(tempfile.gettempdir()) / "bratstvo_test_storage")
os.environ["SUPERUSER_TELEGRAM_IDS"] = ""
os.environ.pop("DEV_TELEGRAM_ID", None)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from api.auth import TelegramIdentity, get_current_user, get_telegram_identity  # noqa: E402
from api.main import app  # noqa: E402
from database.db import async_session, engine  # noqa: E402
from database.models import (  # noqa: E402
    ROLE_CELL_LEADER,
    ROLE_COORDINATOR,
    ROLE_FEDERAL,
    ROLE_LEADER,
    ROLE_SUPERUSER,
    Base,
    CoordinatorRegion,
    Region,
    UniversityCell,
    User,
)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def login(user):
    """Подменяет разбор initData: подпись проверяется в tests/test_auth.py отдельно."""
    app.dependency_overrides[get_current_user] = lambda: user


def login_as_identity(telegram_id: int, full_name: str = "Незнакомый Человек"):
    """То же самое, но для api/routers/register.py — там ещё нет User,
    зависимость get_telegram_identity не резолвит его (см. api/auth.py)."""
    app.dependency_overrides[get_telegram_identity] = lambda: TelegramIdentity(telegram_id, full_name)


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture(autouse=True)
def no_telegram(monkeypatch):
    """Ни один тест не должен ходить в Telegram: уведомления гасим на входе."""
    monkeypatch.setattr("utils.notify.get_notifier_bot", lambda: None)


@pytest_asyncio.fixture
async def session():
    async with async_session() as session:
        yield session


@pytest_asyncio.fixture
async def world(session):
    """Два региона, руководители, координатор на первый регион и федеральный."""
    moscow = Region(name="Москва")
    tula = Region(name="Тула")
    session.add_all([moscow, tula])
    await session.flush()

    leader_moscow = User(full_name="Иванов Иван", role=ROLE_LEADER, telegram_id=1001)
    leader_tula = User(full_name="Петров Пётр", role=ROLE_LEADER, telegram_id=1002)
    coordinator = User(full_name="Сидоров Семён", role=ROLE_COORDINATOR, telegram_id=1003)
    federal = User(full_name="Фёдоров Фёдор", role=ROLE_FEDERAL, telegram_id=1004)
    session.add_all([leader_moscow, leader_tula, coordinator, federal])
    await session.flush()

    moscow.leader_user_id = leader_moscow.id
    tula.leader_user_id = leader_tula.id
    session.add(CoordinatorRegion(coordinator_user_id=coordinator.id, region_id=moscow.id))
    await session.flush()

    cell_leader = User(full_name="Кузнецов Иван", role=ROLE_CELL_LEADER, telegram_id=1005)
    session.add(cell_leader)
    await session.flush()
    mgimo = UniversityCell(region_id=moscow.id, name="МГИМО", leader_user_id=cell_leader.id)
    session.add(mgimo)
    await session.flush()

    superuser = User(full_name="Технический Админ", role=ROLE_SUPERUSER, telegram_id=1006)
    session.add(superuser)
    await session.commit()
    await session.refresh(mgimo)
    await session.refresh(superuser)

    return {
        "moscow": moscow,
        "tula": tula,
        "leader_moscow": leader_moscow,
        "leader_tula": leader_tula,
        "coordinator": coordinator,
        "federal": federal,
        "cell_leader": cell_leader,
        "mgimo": mgimo,
        "superuser": superuser,
    }
