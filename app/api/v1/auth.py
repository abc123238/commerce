"""认证接口：注册 / 登录 / 刷新令牌 / 当前用户。

登录成功后除响应体外，同时种下 HttpOnly 会话 cookie（前端 JS 不可读），
配合 deps.get_current_user 的 cookie 优先解析，实现安全的会话保持。
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.config import settings
from app.core.rate_limit import limiter
from app.core.security import (
    ACCESS_TOKEN_COOKIE,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.models.cart import Cart
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookie(response: Response, access_token: str) -> None:
    """种下 HttpOnly 会话 cookie：JS 不可读，减少 XSS 窃取面。

    未登录端点（登录/注册）也会命中限流：以 IP 为维度，抑制爆破与批量注册。
    """
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.jwt_access_expire_minutes * 60,
        path="/",
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(f"{settings.rate_limit_public}/minute")
async def register(
    request: Request,
    response: Response,
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing:
        # 统一为 400 + 通用文案，避免通过 409 状态码枚举已注册邮箱
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "注册失败，请稍后重试")

    user = User(email=payload.email, password_hash=hash_password(payload.password))
    db.add(user)
    await db.flush()  # 获取自增 id
    db.add(Cart(user_id=user.id))  # 注册即创建购物车
    await db.commit()

    tokens = TokenResponse(
        access_token=create_access_token(user.id, user.role),
        refresh_token=create_refresh_token(user.id),
    )
    _set_auth_cookie(response, tokens.access_token)
    return tokens


@router.post("/login", response_model=TokenResponse)
@limiter.limit(f"{settings.rate_limit_public}/minute")
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    user = await db.scalar(select(User).where(User.email == payload.email))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误")
    tokens = TokenResponse(
        access_token=create_access_token(user.id, user.role),
        refresh_token=create_refresh_token(user.id),
    )
    _set_auth_cookie(response, tokens.access_token)
    return tokens


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit(f"{settings.rate_limit_public}/minute")
async def refresh(
    request: Request,
    response: Response,
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    try:
        data = decode_token(payload.refresh_token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效的刷新令牌")
    if data.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "令牌类型不正确")

    user = await db.scalar(select(User).where(User.id == int(data["sub"])))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    tokens = TokenResponse(
        access_token=create_access_token(user.id, user.role),
        refresh_token=payload.refresh_token,
    )
    _set_auth_cookie(response, tokens.access_token)
    return tokens


@router.post("/logout")
async def logout(response: Response) -> dict:
    """清除会话 cookie（前端 token 不落 localStorage，无需客户端处理）。"""
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
