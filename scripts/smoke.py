"""基础设施冒烟测试：应用 / 数据库 / Redis / Qdrant / LLM 连通性。

Qdrant 与 LLM 为可选项：未就绪时仅告警，不阻塞整体结果。
用法：uv run python -m scripts.smoke
"""
import asyncio
import sys

import httpx
from sqlalchemy import text

from app.config import settings
from app.db.session import engine


async def check(name: str, ok: bool, extra: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" | {extra}" if extra else ""))
    return ok


async def check_db() -> bool:
    try:
        async with engine.connect() as conn:
            val = await conn.scalar(text("SELECT 1"))
        return await check("PostgreSQL 连通", val == 1)
    except Exception as exc:  # noqa: BLE001
        return await check("PostgreSQL 连通", False, str(exc)[:120])


async def check_redis() -> bool:
    try:
        import redis

        r = redis.Redis.from_url(settings.redis_url)
        pong = r.ping()
        r.close()
        return await check("Redis 连通", bool(pong))
    except Exception as exc:  # noqa: BLE001
        return await check("Redis 连通", False, str(exc)[:120])


async def check_qdrant() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.qdrant_url}/collections")
            resp.raise_for_status()
        return await check("Qdrant 连通", True)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Qdrant 连通 | {str(exc)[:120]}（W2 才需要，可忽略）")
        return True


async def check_llm() -> bool:
    if not settings.anthropic_base_url or not settings.anthropic_auth_token:
        print("[WARN] LLM 冒烟 | 未配置 ANTHROPIC_BASE_URL / AUTH_TOKEN，跳过")
        return True
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.anthropic_base_url.rstrip('/')}/v1/messages",
                headers={
                    "x-api-key": settings.anthropic_auth_token,
                    "authorization": f"Bearer {settings.anthropic_auth_token}",
                    "content-type": "application/json",
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": settings.anthropic_model,
                    "max_tokens": 8,
                    "messages": [{"role": "user", "content": "ping"}],
                },
            )
        ok = resp.status_code == 200
        extra = resp.text[:120] if not ok else ""
        return await check("LLM 认证冒烟", ok, extra)
    except Exception as exc:  # noqa: BLE001
        return await check("LLM 认证冒烟", False, str(exc)[:120])


async def check_health() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("http://127.0.0.1:8000/health")
        ok = resp.status_code == 200 and resp.json().get("status") == "ok"
        return await check("应用 /health", ok, resp.text[:120])
    except Exception as exc:  # noqa: BLE001
        return await check("应用 /health", False, str(exc)[:120])


async def main() -> int:
    results = await asyncio.gather(
        check_health(), check_db(), check_redis(), check_qdrant(), check_llm()
    )
    failed = sum(1 for ok in results if not ok)
    if failed:
        print(f"\n❌ 冒烟测试结束：{failed} 项失败")
        return 1
    print("\n✅ 冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
