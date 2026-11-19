"""共享 Redis 异步客户端：承载下单待确认快照这类短生命周期状态。

限流走 slowapi 自带的 storage（其内部实现不宜作为业务状态的入口），
这里用同一个 settings.redis_url 另建一个独立的共享连接池。

按事件循环缓存：pytest 每个用例跑在独立事件循环上，跨循环复用连接会抛
"Event loop is closed"（与 app/tests/conftest.py 里用 NullPool 同因）。

socket_timeout=2：Redis 不可用时必须快速失败，否则会撞上
ToolRegistry 的 10 秒超时，把「快照服务异常」变成「工具执行超时」。
"""
import asyncio
from weakref import WeakKeyDictionary

import redis.asyncio as aioredis

from app.config import settings

_clients: WeakKeyDictionary[asyncio.AbstractEventLoop, aioredis.Redis] = WeakKeyDictionary()


def get_redis() -> aioredis.Redis:
    """惰性建连：应用启动不依赖 Redis 可用（与限流一致）。"""
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        client = aioredis.Redis.from_url(
            settings.redis_url, decode_responses=True, socket_timeout=2
        )
        _clients[loop] = client
    return client
