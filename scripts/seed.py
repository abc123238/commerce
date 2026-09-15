"""初始化种子数据：管理员/演示用户、分类、商品。

幂等且收敛：按唯一键（邮箱 / 分类名 / 商品名）检查；
已存在的商品会按目录刷新分类/描述/价格/库存/状态，
多余且无商品的分类会被清理。
用法：uv run python -m scripts.seed
"""
import asyncio

from sqlalchemy import func, select

from app.core.security import hash_password, verify_password as _verify_password
from app.db.models.cart import Cart, CartItem
from app.db.models.order import Order, OrderItem, OrderStatus
from app.db.models.product import Category, Product
from app.db.models.user import User
from app.db.session import SessionLocal

# (分类名, [(商品名, 描述, 价格, 库存, 状态), ...])
CATALOG: list[tuple[str, list[tuple[str, str, str, int, str]]]] = [
    (
        "电子产品",
        [
            (
                "无线蓝牙耳机",
                "主动降噪，蓝牙 5.3，续航 30 小时，支持双设备连接，Type-C 快充。",
                "399.00",
                100,
                "on",
            ),
            (
                "机械键盘",
                "87 键热插拔，Gateron 红轴，PBT 键帽，全键无冲，支持三模连接。",
                "299.00",
                80,
                "on",
            ),
            (
                "智能手表",
                "1.43 英寸 AMOLED 屏，血氧/心率/睡眠监测，5ATM 防水，续航 14 天。",
                "1299.00",
                50,
                "on",
            ),
            (
                "便携充电宝",
                "20000mAh 大容量，22.5W 双向快充，支持 PD/QC 协议，可上飞机。",
                "159.00",
                200,
                "on",
            ),
        ],
    ),
    (
        "图书",
        [
            (
                "Python 编程入门",
                "从零开始的 Python 教程，覆盖语法、函数、面向对象与实战项目。",
                "69.00",
                300,
                "on",
            ),
            (
                "深度学习实战",
                "基于 PyTorch 的深度学习实践，涵盖 CNN/RNN/Transformer 与部署。",
                "129.00",
                150,
                "on",
            ),
            (
                "AI 应用开发实战",
                "大模型应用开发指南：Prompt、LLM、RAG、Agent 与生产部署全流程。",
                "99.00",
                120,
                "on",
            ),
        ],
    ),
]

USERS: list[tuple[str, str, str]] = [
    ("admin@example.com", "Admin@123456", "admin"),
    ("demo@example.com", "Demo@123456", "customer"),
]


async def _seed_demo_orders(session) -> None:
    """为 demo 用户造 2 笔演示订单（一已支付、一待支付），幂等。"""
    demo = await session.scalar(select(User).where(User.email == "demo@example.com"))
    if demo is None:
        return
    count = await session.scalar(
        select(func.count()).select_from(Order).where(Order.user_id == demo.id)
    )
    if count:
        print(f"跳过演示订单（demo 已有 {count} 笔）")
        return

    products = list(
        await session.scalars(select(Product).where(Product.status == "on").order_by(Product.id))
    )
    if len(products) < 2:
        return
    cart = await session.scalar(select(Cart).where(Cart.user_id == demo.id))
    old_items = list(
        await session.scalars(select(CartItem).where(CartItem.cart_id == cart.id))
    )
    for item in old_items:
        await session.delete(item)
    await session.flush()  # 先提交删除，避免与新增同 flush 触发唯一约束冲突
    session.add_all(
        [
            CartItem(cart_id=cart.id, product_id=products[0].id, quantity=1),
            CartItem(cart_id=cart.id, product_id=products[1].id, quantity=2),
        ]
    )
    await session.flush()

    # 订单1：待支付
    o1 = Order(order_no="ORDDEMO000000000001", user_id=demo.id, total=0)
    session.add(o1)
    await session.flush()
    o1.total = products[0].price * 1 + products[1].price * 2
    session.add_all(
        [
            OrderItem(order_id=o1.id, product_id=products[0].id, product_name=products[0].name, quantity=1, unit_price=products[0].price),
            OrderItem(order_id=o1.id, product_id=products[1].id, product_name=products[1].name, quantity=2, unit_price=products[1].price),
        ]
    )
    # 订单2：已支付
    o2 = Order(order_no="ORDDEMO000000000002", user_id=demo.id, total=0)
    session.add(o2)
    await session.flush()
    o2.total = products[0].price * 1
    o2.status = OrderStatus.PAID
    o2.paid_at = __import__("datetime").datetime.now()
    session.add(
        OrderItem(order_id=o2.id, product_id=products[0].id, product_name=products[0].name, quantity=1, unit_price=products[0].price)
    )
    print("创建 2 笔演示订单（demo 用户）")


async def seed() -> None:
    async with SessionLocal() as session:
        # 用户
        for email, password, role in USERS:
            user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email, password_hash=hash_password(password), role=role)
                session.add(user)
                await session.flush()
                session.add(Cart(user_id=user.id))
                print(f"创建用户 {email}（{role}）")
            else:
                # 收敛：确保密码与角色可复现（供本地演示）
                if user.role != role or not _verify_password(password, user.password_hash):
                    user.password_hash = hash_password(password)
                    user.role = role
                    print(f"刷新用户 {email}（{role}）")

        catalog_cats = {name for name, _ in CATALOG}
        for cat_name, products in CATALOG:
            cat = await session.scalar(select(Category).where(Category.name == cat_name))
            if cat is None:
                cat = Category(name=cat_name)
                session.add(cat)
                await session.flush()
                print(f"创建分类 {cat_name}")
            for name, desc, price, stock, status in products:
                product = await session.scalar(select(Product).where(Product.name == name))
                if product is None:
                    session.add(
                        Product(
                            category_id=cat.id,
                            name=name,
                            description=desc,
                            price=price,
                            stock=stock,
                            status=status,
                        )
                    )
                    print(f"创建商品 {name} ¥{price}（{cat_name}）")
                else:
                    # 收敛：刷新分类与价格等，修复历史数据漂移
                    if (
                        product.category_id != cat.id
                        or str(product.price) != price
                        or product.stock != stock
                        or product.status != status
                        or product.description != desc
                    ):
                        product.category_id = cat.id
                        product.description = desc
                        product.price = price
                        product.stock = stock
                        product.status = status
                        print(f"刷新商品 {name}（{cat_name}）")

        # 清理：删除不在目录中且已无商品的分类
        cats = list(await session.scalars(select(Category)))
        for cat in cats:
            if cat.name in catalog_cats:
                continue
            still_used = await session.scalar(
                select(Product.id).where(Product.category_id == cat.id).limit(1)
            )
            if still_used is None:
                await session.delete(cat)
                print(f"清理空分类 {cat.name}")

        await _seed_demo_orders(session)
        await session.commit()
    print("✅ 种子数据初始化完成")


if __name__ == "__main__":
    asyncio.run(seed())
