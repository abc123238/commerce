"""鉴权接口集成测试（需要 Postgres）。"""
import uuid

import pytest

pytestmark = pytest.mark.integration

_AUTH = "/api/v1/auth"


def _email() -> str:
    return f"test_{uuid.uuid4().hex[:8]}@example.com"


async def test_register_login_me_refresh(client):
    email, password = _email(), "Test@123456"

    r = await client.post(f"{_AUTH}/register", json={"email": email, "password": password})
    assert r.status_code == 201
    token = r.json()
    assert token["access_token"] and token["refresh_token"]

    me = await client.get(
        f"{_AUTH}/me", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == email
    assert me.json()["role"] == "customer"

    refreshed = await client.post(
        f"{_AUTH}/refresh", json={"refresh_token": token["refresh_token"]}
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]

    login = await client.post(f"{_AUTH}/login", json={"email": email, "password": password})
    assert login.status_code == 200
    assert login.json()["access_token"]


async def test_register_duplicate_generic_400(client):
    # 重复注册返回 400 + 通用文案，避免通过状态码枚举已注册邮箱
    email = _email()
    payload = {"email": email, "password": "Test@123456"}
    assert (await client.post(f"{_AUTH}/register", json=payload)).status_code == 201
    dup = await client.post(f"{_AUTH}/register", json=payload)
    assert dup.status_code == 400
    assert "注册失败" in dup.json()["detail"]


async def test_login_wrong_password_401(client):
    email = _email()
    await client.post(f"{_AUTH}/register", json={"email": email, "password": "Test@123456"})
    r = await client.post(
        f"{_AUTH}/login", json={"email": email, "password": "Wrong@123456"}
    )
    assert r.status_code == 401


async def test_me_requires_token(client):
    assert (await client.get(f"{_AUTH}/me")).status_code == 401


async def test_cookie_auth_flow(client):
    """登录响应种下 HttpOnly cookie，后续可仅凭 cookie 访问受保护端点（M1）。"""
    email, password = _email(), "Test@123456"
    r = await client.post(f"{_AUTH}/register", json={"email": email, "password": password})
    assert r.status_code == 201
    assert client.cookies.get("access_token")  # httpx 自动把 Set-Cookie 存入 client

    # 不带 Authorization header，仅凭 cookie
    me = await client.get(f"{_AUTH}/me")
    assert me.status_code == 200
    assert me.json()["email"] == email


async def test_analytics_requires_admin(client):
    """普通顾客访问经营分析端点 → 403（H1 越权防护）。"""
    r = await client.post(f"{_AUTH}/register", json={"email": _email(), "password": "Test@123456"})
    assert r.status_code == 201
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    resp = await client.post(
        "/api/v1/analytics", json={"question": "本月销售额"}, headers=headers
    )
    assert resp.status_code == 403


async def test_chat_requires_auth(client):
    # 鉴权失败在限流/LLM 之前短路，无需依赖服务
    r = await client.post("/api/v1/chat", json={"question": "你好"})
    assert r.status_code == 401
