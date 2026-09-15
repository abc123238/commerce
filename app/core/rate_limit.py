"""限流：slowapi + Redis 存储，按用户（JWT sub）或 IP 维度。"""
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.config import settings
from app.core.security import decode_token


def _request_key(request: Request) -> str:
    """优先按登录用户限流，未登录回退 IP。"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            payload = decode_token(auth[7:])
            if payload.get("type") == "access" and payload.get("sub"):
                return f"user:{payload['sub']}"
        except Exception:  # noqa: BLE001
            pass
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_request_key, storage_uri=settings.redis_url)
