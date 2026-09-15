"""pytest 全局夹具：独立测试数据库 + 共享会话工厂 + 无外部依赖的 API 客户端。

集成测试（pytestmark = integration）依赖已启动的依赖服务：
Postgres（业务库）、Qdrant（混合检索）、Redis（限流）。
可运行 `pytest -m "not integration"` 仅跑纯单测。
"""
import asyncio

import asyncpg
import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import models  # noqa: F401  确保全部模型注册进 Base.metadata
from app.db.base import Base
from app.db.session import get_db
from app.main import app

TEST_DB = "ecommerce_test"


def _test_database_url() -> str:
    return (
        f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{TEST_DB}"
    )


def _create_test_db() -> None:
    async def _inner() -> None:
        conn = await asyncpg.connect(
            user=settings.postgres_user,
            password=settings.postgres_password,
            host=settings.postgres_host,
            port=settings.postgres_port,
            database="postgres",
        )
        try:
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB
            )
            if not exists:
                await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
        finally:
            await conn.close()

    asyncio.run(_inner())


def _create_schema() -> None:
    async def _inner() -> None:
        engine = create_async_engine(_test_database_url())
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_inner())


_create_test_db()
_create_schema()

# 共享测试库的会话工厂：API 请求（依赖覆盖）与测试直接写库（fixture）使用同一实例。
# NullPool：pytest 每个用例独立事件循环，池化连接会跨循环残留并触发
# "Event loop is closed"，故关闭连接池复用，连接在单用例循环内创建与释放。
_session_factory = async_sessionmaker(
    create_async_engine(_test_database_url(), poolclass=NullPool),
    expire_on_commit=False,
)


async def _override_get_db():
    async with _session_factory() as session:
        yield session


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture
async def db_session():
    """测试内直接读写业务库的会话。"""
    async with _session_factory() as session:
        yield session


@pytest.fixture
async def client():
    """ASGI 内存客户端：请求经完整中间件栈（trace/限流），但依赖 get_db 指向测试库。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
