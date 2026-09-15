"""安全工具：bcrypt 密码哈希、JWT 签发与校验。

JWT 载荷含 sub(用户id)、type(access/refresh)、role、exp。
"""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi.security import OAuth2PasswordBearer

from app.config import settings
from app.db.models.user import User

# HttpOnly cookie 名：登录后除响应体外，同时种下会话 cookie，
# 前端 JS 无法读取，缓解 localStorage 存储 token 的 XSS 窃取风险。
ACCESS_TOKEN_COOKIE = "access_token"

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_v1_prefix}/auth/login",
    auto_error=False,
)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def _create_token(
    user_id: int,
    token_type: str,
    role: str | None = None,
    expires: timedelta | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict = {
        "sub": str(user_id),
        "type": token_type,
        "iat": now,
    }
    if role is not None:
        payload["role"] = role
    if expires is not None:
        payload["exp"] = now + expires
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int, role: str) -> str:
    return _create_token(
        user_id, "access", role, timedelta(minutes=settings.jwt_access_expire_minutes)
    )


def create_refresh_token(user_id: int) -> str:
    return _create_token(user_id, "refresh", expires=timedelta(days=settings.jwt_refresh_expire_days))


def decode_token(token: str) -> dict:
    """校验并解析 JWT；无效/过期抛 jwt.PyJWTError（由调用方转 401）。"""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
