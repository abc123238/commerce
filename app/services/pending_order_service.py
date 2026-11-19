"""Agent 待确认下单快照：两阶段确认的载体，存 Redis。

key 设计 agent:order:pending:{user_id} —— 每用户仅一个槽位（后一次 prepare 覆盖旧的）。
单槽位换来两个性质：
  1. confirm_order 无需任何参数，模型无法指认别的清单；
  2. 用户改了商品必须重新 prepare，旧清单天然失效。
user_id 由工具闭包注入、绝不来自模型入参，因此不存在跨用户串号。
"""
import json

from redis.asyncio import Redis

_PREFIX = "agent:order:pending"


def _key(user_id: int) -> str:
    return f"{_PREFIX}:{user_id}"


async def save_pending_order(
    client: Redis, user_id: int, snapshot: dict, ttl_seconds: int
) -> None:
    await client.set(
        _key(user_id), json.dumps(snapshot, ensure_ascii=False), ex=ttl_seconds
    )


async def take_pending_order(client: Redis, user_id: int) -> dict | None:
    """原子取出并删除（GETDEL）：并发 confirm 只有一个能拿到快照。

    不用「先 GET 建单再 DEL」——那会让两个并发请求都读到快照、各自下一单。
    需要 Redis >= 6.2；更低版本改用 Lua EVAL 做等价的 GET+DEL。
    """
    raw = await client.getdel(_key(user_id))
    return json.loads(raw) if raw else None


async def peek_pending_order(client: Redis, user_id: int) -> dict | None:
    """只读查看，不消费（调试与测试用）。"""
    raw = await client.get(_key(user_id))
    return json.loads(raw) if raw else None


async def clear_pending_order(client: Redis, user_id: int) -> None:
    await client.delete(_key(user_id))
