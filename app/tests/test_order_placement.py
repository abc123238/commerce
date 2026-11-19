"""Agent 下单写工具集成测试（需要 Postgres + Redis）。

守三类不变量，它们都是「prompt 挡不住、只能靠结构保证」的部分：
1. 两阶段确认不可绕过——无快照、同轮 prepare+confirm 都必须失败；
2. 快照恰好被消费一次——并发 confirm 只能成一单、库存只扣一次；
3. 越权不可发生——user_id 只经闭包注入，A 的快照对 B 不可见。
"""
import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.agent.tools.order_write_tools import build_order_write_tools
from app.config import settings
from app.core.redis import get_redis
from app.core.security import hash_password
from app.db.models import (
    Category,
    Order,
    OrderStatus,
    Payment,
    Product,
    User,
)
from app.services import pending_order_service
from app.tests.conftest import _session_factory

pytestmark = pytest.mark.integration


def _key(user_id: int) -> str:
    return f"agent:order:pending:{user_id}"


@pytest.fixture
async def user_id(db_session):
    user = User(
        email=f"ow_{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Test@123456"),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    yield user.id
    await pending_order_service.clear_pending_order(get_redis(), user.id)


async def _product(db_session, price: str = "100.00", stock: int = 10, status: str = "on"):
    cat = Category(name=f"cat_{uuid.uuid4().hex[:8]}")
    db_session.add(cat)
    await db_session.flush()
    product = Product(
        category_id=cat.id,
        name=f"商品_{uuid.uuid4().hex[:6]}",
        description="",
        price=price,
        stock=stock,
        status=status,
    )
    db_session.add(product)
    await db_session.commit()
    await db_session.refresh(product)
    return product


def _tools(db, user_id: int, run_id: str = "run-1") -> dict:
    return {t.name: t for t in build_order_write_tools(db, user_id, run_id)}


async def _order_count(db, user_id: int) -> int:
    """只数本用例用户的订单：测试库在文件之间共享，全局计数会被别的用例污染。"""
    return await db.scalar(
        select(func.count()).select_from(Order).where(Order.user_id == user_id)
    )


async def _stock_of(db, product_id: int) -> int:
    """只取列、绕过身份映射：并发用例里扣减发生在别的会话，缓存实例是陈旧的。"""
    return await db.scalar(select(Product.stock).where(Product.id == product_id))


# ---------- prepare_order ----------


async def test_prepare_writes_snapshot_with_ttl(db_session, user_id):
    product = await _product(db_session, price="399.00", stock=5)
    tools = _tools(db_session, user_id)

    text = await tools["prepare_order"].ainvoke(
        {"items": [{"product_id": product.id, "quantity": 2}]}
    )

    assert "¥798.00" in text and "确认" in text
    assert await _order_count(db_session, user_id) == 0  # prepare 绝不建单

    snapshot = await pending_order_service.peek_pending_order(get_redis(), user_id)
    assert snapshot["total"] == "798.00"
    assert snapshot["items"][0]["unit_price"] == "399.00"  # 金额存字符串，防浮点误差
    assert snapshot["items"][0]["quantity"] == 2

    ttl = await get_redis().ttl(_key(user_id))
    assert 0 < ttl <= settings.agent_pending_order_ttl_seconds


async def test_prepare_rejects_bad_items_without_snapshot(db_session, user_id):
    """校验不通过就不写快照——否则会留下一份不可下单的清单。"""
    off = await _product(db_session, status="off")
    low = await _product(db_session, stock=1)
    tools = _tools(db_session, user_id)

    missing = await tools["prepare_order"].ainvoke(
        {"items": [{"product_id": 99999999, "quantity": 1}]}
    )
    assert "不存在" in missing

    offline = await tools["prepare_order"].ainvoke(
        {"items": [{"product_id": off.id, "quantity": 1}]}
    )
    assert "已下架" in offline

    shortage = await tools["prepare_order"].ainvoke(
        {"items": [{"product_id": low.id, "quantity": 2}]}
    )
    assert "库存不足" in shortage

    assert await pending_order_service.peek_pending_order(get_redis(), user_id) is None


async def test_prepare_merges_duplicate_product_ids(db_session, user_id):
    product = await _product(db_session, price="10.00", stock=10)
    tools = _tools(db_session, user_id)

    text = await tools["prepare_order"].ainvoke(
        {"items": [{"product_id": product.id, "quantity": 1}, {"product_id": product.id, "quantity": 2}]}
    )

    assert "¥30.00" in text
    snapshot = await pending_order_service.peek_pending_order(get_redis(), user_id)
    assert len(snapshot["items"]) == 1 and snapshot["items"][0]["quantity"] == 3


# ---------- confirm_order ----------


async def test_confirm_without_snapshot_fails(db_session, user_id):
    tools = _tools(db_session, user_id)

    text = await tools["confirm_order"].ainvoke({})

    assert "没有找到待确认清单" in text
    assert await _order_count(db_session, user_id) == 0


async def test_confirm_same_run_rejected_and_snapshot_kept(db_session, user_id):
    """同轮 prepare+confirm 必须被拒；且被拒时不消费快照。

    core.py 会在一次 execute_tool 里顺序执行同一 AIMessage 的所有 tool_calls，
    模型完全可以在同一轮先 prepare 再 confirm——「confirm 无参数」挡不住这个时序。
    若守卫放在 GETDEL 之后，快照会被白白消费，用户下一条消息的「确认」必然失败。
    """
    product = await _product(db_session, price="50.00", stock=5)
    tools = _tools(db_session, user_id, run_id="same-run")

    await tools["prepare_order"].ainvoke({"items": [{"product_id": product.id, "quantity": 1}]})
    text = await tools["confirm_order"].ainvoke({})

    assert "本轮刚生成" in text
    assert await _order_count(db_session, user_id) == 0
    assert await pending_order_service.peek_pending_order(get_redis(), user_id) is not None


async def test_confirm_in_next_run_creates_paid_order(db_session, user_id):
    """跨 run（用户真的回了「确认」）才下单：建单 + 模拟支付一步到位。"""
    product = await _product(db_session, price="399.00", stock=5)
    await _tools(db_session, user_id, run_id="run-a")["prepare_order"].ainvoke(
        {"items": [{"product_id": product.id, "quantity": 2}]}
    )

    text = await _tools(db_session, user_id, run_id="run-b")["confirm_order"].ainvoke({})

    order = await db_session.scalar(select(Order).where(Order.user_id == user_id))
    assert f"订单号 {order.order_no}" in text and "已支付" in text
    assert order.status == OrderStatus.PAID
    assert order.paid_at is not None
    assert order.total == Decimal("798.00")
    assert await _stock_of(db_session, product.id) == 3
    payment = await db_session.scalar(select(Payment).where(Payment.order_id == order.id))
    assert payment is not None
    # 快照消费即失效：再确认一次必然失败
    again = await _tools(db_session, user_id, run_id="run-c")["confirm_order"].ainvoke({})
    assert "没有找到待确认清单" in again
    assert await _order_count(db_session, user_id) == 1


async def test_concurrent_confirm_creates_single_order(db_session, user_id):
    """并发 confirm 只能成一单：GETDEL 原子消费快照，输的那个拿不到清单。

    每个并发分支用独立会话与独立工具闭包，贴近真实 HTTP 请求的隔离度。
    """
    product = await _product(db_session, price="30.00", stock=3)
    await _tools(db_session, user_id, run_id="run-a")["prepare_order"].ainvoke(
        {"items": [{"product_id": product.id, "quantity": 2}]}
    )

    async def _confirm() -> str:
        async with _session_factory() as session:
            tool = _tools(session, user_id, run_id="run-b")["confirm_order"]
            return await tool.ainvoke({})

    results = await asyncio.gather(_confirm(), _confirm())

    ok = [r for r in results if "已支付" in r]
    failed = [r for r in results if "没有找到待确认清单" in r]
    assert len(ok) == 1 and len(failed) == 1
    assert await _order_count(db_session, user_id) == 1
    assert await _stock_of(db_session, product.id) == 1  # 3 - 2，只扣一次


async def test_snapshot_is_per_user(db_session, user_id):
    """A 的快照对 B 不可见：confirm 无参数也不存在串号的可能。"""
    other = User(
        email=f"ow_{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Test@123456"),
    )
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)

    product = await _product(db_session, price="20.00", stock=5)
    await _tools(db_session, user_id, run_id="run-a")["prepare_order"].ainvoke(
        {"items": [{"product_id": product.id, "quantity": 1}]}
    )

    text = await _tools(db_session, other.id, run_id="run-b")["confirm_order"].ainvoke({})

    assert "没有找到待确认清单" in text
    assert await _order_count(db_session, user_id) == 0
    # A 的清单仍在，A 自己确认能成
    assert await pending_order_service.peek_pending_order(get_redis(), user_id) is not None


async def test_confirm_uses_db_price_not_snapshot_price(db_session, user_id):
    """prepare 后备改价：订单必须以 DB 实时价为准，否则就是锁价漏洞。"""
    product = await _product(db_session, price="100.00", stock=5)
    await _tools(db_session, user_id, run_id="run-a")["prepare_order"].ainvoke(
        {"items": [{"product_id": product.id, "quantity": 1}]}
    )
    product.price = Decimal("120.00")
    await db_session.commit()

    text = await _tools(db_session, user_id, run_id="run-b")["confirm_order"].ainvoke({})

    order = await db_session.scalar(select(Order).where(Order.user_id == user_id))
    assert order.total == Decimal("120.00")
    assert "价格已变动" in text  # 与清单价不符必须显式告知
