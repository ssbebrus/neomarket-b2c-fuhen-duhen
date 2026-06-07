import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from typing import AsyncGenerator, Any

from src.main import app
from src.db.database import get_db, engine as real_engine
from src.config import settings

# Use the same engine but we will wrap tests in transactions
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
async def test_engine():
    from src.db.base import Base
    async with real_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield real_engine
    await real_engine.dispose()

@pytest.fixture
async def test_db(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """
    Создает новую сессию БД для каждого теста, завернутую в транзакцию с использованием SAVEPOINT.
    Это позволяет откатывать все изменения, даже если код приложения вызывает session.commit().
    """
    connection = await test_engine.connect()
    # Начинаем внешнюю транзакцию
    trans = await connection.begin()
    
    # Создаем сессию, связанную с этим соединением
    Session = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False
    )
    session = Session()

    # Начинаем вложенную транзакцию (SAVEPOINT)
    nested = await connection.begin_nested()

    # При вызове commit() в приложении, мы просто закрываем вложенную транзакцию,
    # но внешняя транзакция остается активной и будет откатана в конце теста.
    # Но чтобы сессия могла продолжать работу после "commit", нам нужно переоткрывать savepoint.
    
    in_cleanup = False

    @event.listens_for(session.sync_session, "after_transaction_end")
    def end_savepoint(session_, transaction):
        nonlocal nested
        if in_cleanup:
            return
        if not connection.closed and not connection.in_nested_transaction():
            # SQLAlchemy 2.0 requires restarting the savepoint manually
            nested = connection.sync_connection.begin_nested()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    yield session

    # Откатываем все изменения к началу теста
    in_cleanup = True
    try:
        await trans.rollback()
    except Exception:
        pass
    finally:
        await session.close()
        await connection.close()
    app.dependency_overrides.clear()

@pytest.fixture
async def client(test_db) -> AsyncGenerator[AsyncClient, None]:
    """
    Клиент для тестирования API. Зависит от test_db, чтобы гарантировать
    использование правильной сессии и транзакции.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
