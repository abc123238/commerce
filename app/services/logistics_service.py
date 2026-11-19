"""模拟物流：确定性轨迹 + 惰性状态推进。

轨迹**不落库**——它以 order.paid_at 为基点、按固定偏移表纯函数推算，
因此无随机、随时间单调递增、同一时刻查询结果可复现（评测可断言）。
落库的只有 shipped_at / completed_at / tracking_no / carrier 这些持久事实。

状态推进是**惰性**的：只在查询物流时发生，不起后台定时任务。
代价是订单状态只在被查询时前进；收益是无需调度器、无需多实例协调，
且「准时」这件事在模拟环境里本来就没有意义。
"""
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.order import Order, OrderStatus, status_cn
from app.services import order_event_service

# 相对 paid_at 的固定轨迹节点；下标即「阶段」
_STAGES: tuple[str, ...] = (
    "已支付，等待商家发货",
    "已揽收，杭州转运中心",
    "运输中：杭州 → 上海",
    "到达上海分拨中心",
    "派送中：上海浦东配送站",
    "已签收",
)
_SHIPPED_STAGE = 1  # 达到该阶段：paid -> shipped
_DELIVERED_STAGE = 5  # 达到该阶段：shipped -> completed
_CARRIER = "顺丰速运（模拟）"


@dataclass(frozen=True)
class TrackingNode:
    name: str
    time: datetime
    reached: bool


@dataclass(frozen=True)
class TrackingInfo:
    order_no: str
    status: str
    carrier: str | None
    tracking_no: str | None
    paid_at: datetime | None
    nodes: list[TrackingNode]


def _as_utc(dt: datetime | None) -> datetime | None:
    """DB 里可能存在 naive datetime（历史 seed 数据），统一按 UTC 解释。

    否则 naive 与 aware 相减会抛 TypeError，或按本地时区偏移导致轨迹整体错位。
    """
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _elapsed_seconds(paid_at: datetime, now: datetime) -> float:
    # 两侧都归一化：naive 与 aware 直接相减会抛 TypeError
    paid_at, now = _as_utc(paid_at), _as_utc(now)
    # 钳负：时钟漂移或写入未来时间时，不能产生「负进度」把轨迹算空
    return max(0.0, (now - paid_at).total_seconds())


def build_tracking_nodes(
    paid_at: datetime, now: datetime, stage_seconds: int
) -> list[TrackingNode]:
    """返回全部阶段的节点（含未到达），reached 标记是否已发生。纯函数，无 IO。"""
    paid_at = _as_utc(paid_at)
    elapsed = _elapsed_seconds(paid_at, now)
    return [
        TrackingNode(
            name=name,
            time=paid_at + timedelta(seconds=index * stage_seconds),
            reached=index * stage_seconds <= elapsed,
        )
        for index, name in enumerate(_STAGES)
    ]


def current_stage_index(paid_at: datetime, now: datetime, stage_seconds: int) -> int:
    """当前所处的阶段下标（0..len(_STAGES)-1）。"""
    elapsed = _elapsed_seconds(paid_at, now)
    return min(int(elapsed // stage_seconds), len(_STAGES) - 1)


def _tracking_no(order_no: str) -> str:
    """由 order_no 确定性生成运单号（不用随机数，保证可复现）。"""
    return "SF" + hashlib.sha1(order_no.encode()).hexdigest()[:12].upper()


async def advance_order_status(db: AsyncSession, order_no: str, user_id: int) -> Order:
    """惰性推进订单状态：paid -> shipped -> completed，幂等。

    - FOR UPDATE 行锁：并发查询只有一个事务能完成某一次跃迁。
    - 状态前置判断保证幂等：每个跃迁都要求 status 处于前置状态，故最多发生一次。
    - shipped_at / completed_at 写的是 paid_at + 固定偏移（而非 now），
      因此无论订单在哪个时刻被查询，落库时间都与轨迹一致、可复现。
    - 过滤 user_id：他人订单一律 404，避免越权查询顺带推进别人的订单。
    """
    order = await db.scalar(
        select(Order)
        .where(Order.order_no == order_no, Order.user_id == user_id)
        .with_for_update()
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")

    paid_at = _as_utc(order.paid_at)
    if paid_at is None:
        # 未支付（created / cancelled）：没有 paid_at 就没有物流，也不推进
        return order

    stage = settings.logistics_stage_seconds
    elapsed = _elapsed_seconds(paid_at, datetime.now(timezone.utc))
    changed = False

    if order.status == OrderStatus.PAID and elapsed >= _SHIPPED_STAGE * stage:
        order.status = OrderStatus.SHIPPED
        order.shipped_at = paid_at + timedelta(seconds=_SHIPPED_STAGE * stage)
        tracking_no = _tracking_no(order.order_no)
        order.tracking_no = order.tracking_no or tracking_no
        order.carrier = order.carrier or _CARRIER
        # 事件时间用业务计划时间，而非查询时刻，保证停留时长可复现
        await order_event_service.record_event(
            db,
            order_id=order.id,
            to_status=OrderStatus.SHIPPED,
            from_status=OrderStatus.PAID,
            occurred_at=order.shipped_at,
            source="logistics_lazy",
            actor_user_id=user_id,
            meta={"stage": _SHIPPED_STAGE, "tracking_no": order.tracking_no},
        )
        changed = True
    # 注意这里读的是刚被上面改过的 order.status：
    # 长时间未查询的订单可在一次请求内连跳两态，重复请求则因已是 completed 而不匹配
    if order.status == OrderStatus.SHIPPED and elapsed >= _DELIVERED_STAGE * stage:
        order.status = OrderStatus.COMPLETED
        order.completed_at = paid_at + timedelta(seconds=_DELIVERED_STAGE * stage)
        await order_event_service.record_event(
            db,
            order_id=order.id,
            to_status=OrderStatus.COMPLETED,
            from_status=OrderStatus.SHIPPED,
            occurred_at=order.completed_at,
            source="logistics_lazy",
            actor_user_id=user_id,
            meta={"stage": _DELIVERED_STAGE},
        )
        changed = True

    if changed:
        await db.commit()
        await db.refresh(order)
    return order


async def get_tracking(db: AsyncSession, order_no: str, user_id: int) -> TrackingInfo:
    """查询订单物流轨迹（会顺带幂等推进状态）。他人订单 404。"""
    order = await advance_order_status(db, order_no, user_id)
    paid_at = _as_utc(order.paid_at)
    nodes = (
        build_tracking_nodes(paid_at, datetime.now(timezone.utc), settings.logistics_stage_seconds)
        if paid_at
        else []
    )
    return TrackingInfo(
        order_no=order.order_no,
        status=order.status,
        carrier=order.carrier,
        tracking_no=order.tracking_no,
        paid_at=paid_at,
        nodes=nodes,
    )


async def get_latest_tracking(db: AsyncSession, user_id: int) -> TrackingInfo:
    """查当前用户最近一笔已支付订单的物流。

    用 paid_at 是否为 NULL 判断「已支付」，而不是 status == paid：
    状态是随物流推进的派生量，已发货/已完成的订单同样是已支付订单。
    """
    order_no = await db.scalar(
        select(Order.order_no)
        .where(Order.user_id == user_id, Order.paid_at.is_not(None))
        .order_by(Order.paid_at.desc())
        .limit(1)
    )
    if order_no is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "没有可查询物流的订单（需要已支付订单）"
        )
    return await get_tracking(db, order_no, user_id)


def format_tracking_text(info: TrackingInfo) -> str:
    """渲染成给 LLM 阅读的文本：已到达节点 + 下一站预告。"""
    if info.paid_at is None or not info.nodes:
        return f"订单 {info.order_no}：{status_cn(info.status)}，尚未支付，暂无物流信息。"

    header = f"订单 {info.order_no}：{status_cn(info.status)}"
    meta = (
        f"承运商：{info.carrier or '待发货'}"
        f"｜运单号：{info.tracking_no or '发货后生成'}"
    )
    reached = [n for n in info.nodes if n.reached]
    lines = [f"- {n.time:%m-%d %H:%M} {n.name}" for n in reached]

    pending = [n for n in info.nodes if not n.reached]
    if pending:
        nxt = pending[0]
        lines.append(f"- 下一站：{nxt.name}（预计 {nxt.time:%m-%d %H:%M}）")
    else:
        lines.append("- 物流已完结。")

    return "\n".join([header, meta, "物流轨迹（UTC）：", *lines])
