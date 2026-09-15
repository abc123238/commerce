"""订单业务闭环验证脚本（下单 → 支付 → 取消/库存回补）。

用法：uv run python scripts/verify_order_flow.py
"""
from decimal import Decimal

import httpx

BASE = "http://127.0.0.1:8000"
CLIENT = httpx.Client(base_url=BASE, timeout=10.0)


def check(name: str, ok: bool, extra: str = "") -> None:
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" | {extra}" if extra else ""))
    if not ok:
        raise SystemExit(1)


def main() -> None:
    # 1. 管理员登录（用于准备商品，若已有商品则跳过）
    admin_token = None
    r = CLIENT.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "Admin@123456"},
    )
    if r.status_code == 200:
        admin_token = r.json()["access_token"]

    # 2. 获取商品列表，至少要有 2 个可买商品
    r = CLIENT.get("/api/v1/products", params={"page": 1, "size": 10})
    check("获取商品列表", r.status_code == 200)
    products = r.json()["items"]
    check("存在可购买商品", len(products) >= 2, f"共 {len(products)} 个")
    target = [p for p in products if p["status"] == "on" and p["stock"] > 0][:2]
    check("存在在售且有库存的商品", len(target) >= 2)
    p1, p2 = target[0], target[1]
    stock_p1_before = p1["stock"]
    stock_p2_before = p2["stock"]
    print(f"    商品 {p1['name']} 库存 {stock_p1_before}，商品 {p2['name']} 库存 {stock_p2_before}")

    # 3. 注册新用户并登录
    import secrets
    import string

    suffix = "".join(secrets.choice(string.ascii_lowercase) for _ in range(6))
    email = f"user_{suffix}@example.com"
    r = CLIENT.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Pass@123456"},
    )
    check("注册用户", r.status_code == 201)
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 4. 加购（p1 x2, p2 x1）
    r = CLIENT.post(
        "/api/v1/cart/items", headers=headers, json={"product_id": p1["id"], "quantity": 2}
    )
    check("加购商品1 x2", r.status_code == 201)
    r = CLIENT.post(
        "/api/v1/cart/items", headers=headers, json={"product_id": p2["id"], "quantity": 1}
    )
    check("加购商品2 x1", r.status_code == 201)
    r = CLIENT.get("/api/v1/cart", headers=headers)
    cart = r.json()
    expect_total = Decimal(p1["price"]) * 2 + Decimal(p2["price"])
    check("购物车金额正确", Decimal(cart["total"]) == expect_total, f"total={cart['total']}")

    # 5. 下单
    r = CLIENT.post("/api/v1/orders", headers=headers)
    check("创建订单", r.status_code == 201, str(r.text)[:200] if r.status_code != 201 else "")
    order = r.json()
    order_no = order["order_no"]
    check("订单金额正确", Decimal(order["total"]) == expect_total, f"total={order['total']}")

    # 6. 库存已扣减
    r = CLIENT.get(f"/api/v1/products/{p1['id']}")
    stock_p1_after = r.json()["stock"]
    check(
        "下单后库存扣减",
        stock_p1_after == stock_p1_before - 2,
        f"{p1['name']} {stock_p1_before} -> {stock_p1_after}",
    )

    # 7. 模拟支付
    r = CLIENT.post(f"/api/v1/orders/{order_no}/pay", headers=headers)
    check("模拟支付", r.status_code == 200)
    check("支付后状态为 paid", r.json()["status"] == "paid")
    check("支付后已扣库存不回补", CLIENT.get(f"/api/v1/products/{p1['id']}").json()["stock"] == stock_p1_before - 2)

    # 8. 支付后不能取消
    r = CLIENT.post(f"/api/v1/orders/{order_no}/cancel", headers=headers)
    check("已支付订单不可取消", r.status_code == 400)

    # 9. 再下一单用于取消测试（先重新加购，下单会清空购物车）
    r = CLIENT.post(
        "/api/v1/cart/items", headers=headers, json={"product_id": p1["id"], "quantity": 1}
    )
    check("取消测试前重新加购", r.status_code == 201)
    r = CLIENT.post("/api/v1/orders", headers=headers)
    check("创建待取消订单", r.status_code == 201)
    order2 = r.json()
    r = CLIENT.post(f"/api/v1/orders/{order2['order_no']}/cancel", headers=headers)
    check("取消未支付订单", r.status_code == 200)
    check("取消后状态为 cancelled", r.json()["status"] == "cancelled")
    r = CLIENT.get(f"/api/v1/products/{p1['id']}")
    stock_p1_cancelled = r.json()["stock"]
    # 首单(已支付)仍扣 2 件，第二单(已取消)回补 1 件 => 恢复为 stock_p1_after
    check(
        "取消后库存回补",
        stock_p1_cancelled == stock_p1_after,
        f"{p1['name']} {stock_p1_after} -> {stock_p1_cancelled}",
    )

    # 10. 订单列表包含两笔
    r = CLIENT.get("/api/v1/orders", headers=headers)
    check("订单列表", r.status_code == 200 and r.json()["total"] == 2, f"total={r.json()['total']}")

    print("\n✅ 业务闭环全部通过")


if __name__ == "__main__":
    try:
        main()
    finally:
        CLIENT.close()
