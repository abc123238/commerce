"""下单写工具：两阶段确认（prepare_order -> confirm_order）。

安全设计（写操作是这一层与只读工具的本质区别）：
1. confirm_order **不接受任何商品参数**，只消费 Redis 里最近一次 prepare_order
   生成的待确认快照；没有快照必然失败——模型即便不听话也无法跳过确认，
   也无法指定任意商品下单。
2. user_id 由工具工厂闭包注入，两个工具的入参 schema 里都没有 user_id；
   快照 key 也含 user_id，A 用户的 confirm 读不到 B 的快照。
3. run_id 绑定：同一次 agent 运行内先 prepare 再 confirm 会被拒绝。
   这不是多余的——app/agent/nodes/core.py 会在一次 execute_tool 里按顺序执行
   同一 AIMessage 的所有 tool_calls，模型完全可以在同一轮先 prepare 再 confirm，
   此时快照已存在，「confirm 无参数」只挡得住参数、挡不住时序。
   守卫依赖「一次 HTTP 请求 = 一个 run_id」；若将来改为跨请求复用
   conversation_id，此守卫会失效，必须改成别的确认凭据。
   守卫在消费快照之前只读生效：被它拒掉的那次调用不消费快照，
   用户下一条消息里真正的「确认」才拿得到清单。

写面收窄在这一个文件里便于审计；只读工具模块保持零 Redis 依赖，可脱离 Redis 单测。
"""
from decimal import Decimal

import redis as redis_lib
from fastapi import HTTPException
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.redis import get_redis
from app.db.models.product import Product
from app.services import order_service, pending_order_service

_UNIT_PRICE_MAX_STOCK_HINT = "请调整商品或数量后重新调用 prepare_order。"


class _OrderItemIn(BaseModel):
    product_id: int = Field(gt=0, description="商品 ID，必须来自 query_product 的返回结果")
    quantity: int = Field(gt=0, le=99, description="购买数量")


def _snapshot_unavailable() -> str:
    return "下单功能暂时不可用（待确认快照服务异常），请稍后再试。请勿承诺已下单。"


def build_order_write_tools(db: AsyncSession, user_id: int, run_id: str) -> list:
    """工厂：把 db 会话、当前用户与本次运行的 run_id 绑定进工具闭包。"""

    @tool
    async def prepare_order(items: list[_OrderItemIn]) -> str:
        """生成待确认订单清单（不会创建订单，可安全调用）。

        核对商品是否在售、库存是否充足，把清单写入服务端待确认快照并返回。
        你必须把清单原样复述给用户并明确询问是否确认；只有用户明确确认后，
        才能在下一轮调用 confirm_order。
        商品 ID 必须来自 query_product 的返回结果，禁止猜测或编造。
        一次调用可以带多个商品，不必逐个查询。

        Args:
            items: 购买清单，元素形如 {"product_id": 1, "quantity": 2}
        """
        merged: dict[int, int] = {}
        for item in items:
            merged[item.product_id] = merged.get(item.product_id, 0) + item.quantity
        if not merged:
            return "待确认清单为空：请先说明要购买哪些商品以及各自数量。"

        products = {
            p.id: p for p in await db.scalars(select(Product).where(Product.id.in_(merged)))
        }
        problems: list[str] = []
        for product_id in sorted(merged):
            product = products.get(product_id)
            if product is None:
                problems.append(f"商品 ID {product_id} 不存在")
            elif product.status != "on":
                problems.append(f"商品 {product.name} 已下架")
            elif product.stock < merged[product_id]:
                problems.append(f"商品 {product.name} 库存不足（仅剩 {product.stock}）")
        if problems:
            # 校验不通过就不写快照，避免留下一份不可下单的清单
            return (
                "无法生成待确认清单：\n"
                + "\n".join(f"- {p}" for p in problems)
                + f"\n{_UNIT_PRICE_MAX_STOCK_HINT}"
            )

        snapshot_items = []
        total = Decimal("0")
        for product_id in sorted(merged):
            product = products[product_id]
            quantity = merged[product_id]
            total += product.price * quantity
            snapshot_items.append(
                {
                    "product_id": product.id,
                    "product_name": product.name,
                    # 金额存字符串：JSON 浮点会丢精度，Decimal(str) 往返才精确
                    "unit_price": str(product.price),
                    "quantity": quantity,
                }
            )

        ttl = settings.agent_pending_order_ttl_seconds
        try:
            await pending_order_service.save_pending_order(
                get_redis(),
                user_id,
                {"run_id": run_id, "items": snapshot_items, "total": str(total)},
                ttl,
            )
        except redis_lib.RedisError:
            # fail-closed：宁可下单不可用，也不能跳过快照让用户直接下单
            return _snapshot_unavailable()

        lines = [
            f"- {i['product_name']}（ID {i['product_id']}）× {i['quantity']}，"
            f"单价 ¥{Decimal(i['unit_price']):.2f}，"
            f"小计 ¥{Decimal(i['unit_price']) * i['quantity']:.2f}"
            for i in snapshot_items
        ]
        return (
            f"待确认订单清单（请向用户复述并等待明确确认，{ttl // 60} 分钟内有效）：\n"
            + "\n".join(lines)
            + f"\n合计：¥{total:.2f}\n"
            "请回复「确认」以下单支付。"
        )

    @tool
    async def confirm_order() -> str:
        """提交下单并完成模拟支付（写操作，不可撤销）。

        本工具不接受任何商品参数，只消费最近一次 prepare_order 生成的待确认清单。
        因此必须在用户对该清单做出明确确认之后再调用：没有待确认清单、清单已过期、
        或清单是你本轮刚生成而用户尚未确认时，都会失败，此时需重新调用 prepare_order。
        不支持取消已支付订单；下单即完成模拟支付。
        """
        # 先只读检查、后原子消费：同轮守卫若在 GETDEL 之后，模型同一轮
        # 先 prepare 再 confirm 会把用户的清单白白消费掉，等用户真的回了
        # 「确认」，下一条消息（新 run）反而报「没有待确认清单」。
        try:
            snapshot = await pending_order_service.peek_pending_order(get_redis(), user_id)
        except redis_lib.RedisError:
            return _snapshot_unavailable()

        if snapshot is not None and snapshot.get("run_id") == run_id:
            return (
                "下单失败：该清单是本轮刚生成的，用户尚未确认。"
                "请先把清单复述给用户并等待其明确确认（如「确认」「下单」）后再提交。"
            )

        try:
            snapshot = await pending_order_service.take_pending_order(get_redis(), user_id)
        except redis_lib.RedisError:
            return _snapshot_unavailable()

        if snapshot is None:
            return (
                "下单失败：没有找到待确认清单（尚未生成或已超过有效期）。"
                "请先调用 prepare_order 生成清单并请用户确认。"
            )

        items = [(i["product_id"], i["quantity"]) for i in snapshot["items"]]
        try:
            order = await order_service.create_order(db, user_id, items, source="agent")
        except HTTPException as exc:
            return f"下单失败：{exc.detail}。{_UNIT_PRICE_MAX_STOCK_HINT}"

        # 支付单独兜底：create_order 已 commit，订单不会回滚，
        # 所以失败时要如实告知「订单已建、可去订单页重试支付」而不是让用户重下一单。
        try:
            order = await order_service.pay_order(
                db, order.order_no, user_id, source="agent"
            )
        except HTTPException as exc:
            return (
                f"订单已创建但支付失败：{exc.detail}。"
                f"订单号 {order.order_no}，可在「我的订单」页面重新支付。"
            )

        items_text = "、".join(
            f"{i['product_name']}×{i['quantity']}" for i in snapshot["items"]
        )
        stage = settings.logistics_stage_seconds
        lines = [
            f"下单成功：订单号 {order.order_no}，商品 {items_text}，"
            f"实付 ¥{order.total:.2f}，状态：已支付（模拟支付）。"
        ]
        # 下单必须以 DB 实时价为准；若与清单价不符要显式告知，否则等于锁价漏洞
        if Decimal(snapshot["total"]) != Decimal(order.total):
            lines.append(
                f"注意：下单时价格已变动，实际金额以订单为准"
                f"（原清单 ¥{Decimal(snapshot['total']):.2f}）。"
            )
        lines.append(
            f"支付后约 {stage // 60} 分钟揽收、{5 * stage // 60} 分钟送达，可以让我查询物流。"
        )
        return "\n".join(lines)

    return [prepare_order, confirm_order]
