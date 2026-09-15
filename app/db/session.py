"""异步数据库引擎与会话工厂。"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每个请求一个会话，请求结束自动关闭。

    注意：不通过 `async with SessionLocal()` 进入，避免其隐式开启事务
    与业务层显式 `async with db.begin()` 冲突。
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        await session.close()
