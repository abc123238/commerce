"""订单 / 订单项 / 支付模型。

订单状态机：created -> paid -> shipped -> completed；created -> cancelled。
shipped/completed 由模拟物流惰性推进（见 app/services/logistics_service.py）。
支付为模拟实现（不接真实支付渠道）。
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, func, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class OrderStatus:
    CREATED = "created"
    PAID = "paid"
    SHIPPED = "shipped"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# 状态下 → 中文展示名，集中一处避免工具/接口各自硬编码
STATUS_CN: dict[str, str] = {
    OrderStatus.CREATED: "待支付",
    OrderStatus.PAID: "已支付",
    OrderStatus.SHIPPED: "已发货",
    OrderStatus.COMPLETED: "已完成",
    OrderStatus.CANCELLED: "已取消",
}


def status_cn(status: str) -> str:
    return STATUS_CN.get(status, status)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.CREATED, index=True)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 模拟物流的运单信息：由 order_no 确定性生成（非随机），发货时写入
    tracking_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    carrier: Mapped[str | None] = mapped_column(String(50), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    payment: Mapped["Payment | None"] = relationship(back_populates="order", uselist=False)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    product_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column()
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    order: Mapped["Order"] = relationship(back_populates="items")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True)
    transaction_no: Mapped[str] = mapped_column(String(64), unique=True)
    method: Mapped[str] = mapped_column(String(20), default="mock")
    # status: pending / succeeded / failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    order: Mapped["Order"] = relationship(back_populates="payment")
