"""商品与优惠工具：读取真实商品表 / 计算优惠。"""
from langchain_core.tools import tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.product import Product

# 优惠券规则（演示用）：key 为券码
_COUPONS = {
    "NEWUSER": {"desc": "新客 8 折，最高减 50", "rate": 0.8, "max_discount": 50},
    "WELCOME99": {"desc": "满 99 减 10", "min": 99, "discount": 10},
}


def build_product_tools(db: AsyncSession) -> list:
    @tool
    async def query_product(name: str) -> str:
        """按名称模糊查询商品实时价格与库存。

        Args:
            name: 商品名称关键词，如“蓝牙耳机”
        """
        products = list(
            await db.scalars(
                select(Product)
                .where(Product.name.contains(name))
                .order_by(Product.id)
                .limit(5)
            )
        )
        if not products:
            return f"未找到名称包含「{name}」的商品"
        return "\n".join(
            f"商品 {p.name}：价格={p.price} 元，库存={p.stock}，"
            f"状态={'在售' if p.status == 'on' else '下架'}"
            for p in products
        )

    @tool
    async def calc_discount(subtotal: float, coupon_code: str) -> str:
        """根据小计金额与优惠券码计算应付金额。

        Args:
            subtotal: 购物车/订单小计金额（元）
            coupon_code: 优惠券码，如 NEWUSER、WELCOME99
        """
        rule = _COUPONS.get(coupon_code.strip().upper())
        if rule is None:
            return f"优惠券 {coupon_code} 不存在或已过期，应付 {subtotal:.2f} 元"
        if "rate" in rule:
            discount = min(subtotal * (1 - rule["rate"]), rule["max_discount"])
        else:
            if subtotal < rule["min"]:
                return (
                    f"券 {coupon_code} 需满 {rule['min']} 元可用（当前 {subtotal:.2f}），"
                    f"应付 {subtotal:.2f} 元"
                )
            discount = rule["discount"]
        return (
            f"使用券 {coupon_code}（{rule['desc']}）可优惠 {discount:.2f} 元，"
            f"应付 {subtotal - discount:.2f} 元"
        )

    return [query_product, calc_discount]
