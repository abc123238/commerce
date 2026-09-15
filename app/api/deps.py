"""FastAPI 依赖注入：当前用户解析与角色守卫（RBAC）。"""
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import ACCESS_TOKEN_COOKIE, decode_token, oauth2_scheme
from app.db.models.user import User
from app.db.session import get_db


async def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    # 优先取 HttpOnly cookie（前端不再持有 token），Authorization header 作为兜底，
    # 兼容现有脚本/测试与 OAuth2PasswordBearer 的 OpenAPI 文档流程。
    token = token or request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少访问令牌")
    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效或过期的凭证")
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "令牌类型不正确")

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        # 畸形载荷（缺 sub / 非数字）：视为无效凭证，返回 401 而非 500
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效或过期的凭证")

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    return user


def require_roles(*roles: str):
    """角色守卫：当前用户必须属于给定角色之一，否则 403。"""
    async def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限访问")
        return user

    return checker
