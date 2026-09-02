import asyncio
from collections.abc import AsyncGenerator

import pytest

from vajra.config import settings
from vajra.core.database import close_db, get_async_session, init_db


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_database():
    original_db_url = settings.DATABASE_URL
    settings.DATABASE_URL = "postgresql+asyncpg://vajra:vajra@localhost:5432/vajra_test"
    await init_db()
    yield
    await close_db()
    settings.DATABASE_URL = original_db_url


@pytest.fixture
async def db_session() -> AsyncGenerator:
    async for session in get_async_session():
        yield session
        await session.rollback()
