"""业务闭环验证脚本：注册→登录→商品→购物车→下单→模拟支付→查订单。

docker 全栈部署后用 python scripts/verify_flow.py 跑一遍，
逐步骤打印结果，任一步失败即中止。
"""
import json
import os
import sys
import urllib.error
import urllib.request

# Windows 控制台默认 GBK，中文/货币符号会被截断，统一走 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

BASE = os.environ.get("VERIFY_BASE", "http://127.0.0.1:8000/api/v1")
EMAIL = os.environ.get("VERIFY_EMAIL", "verify@example.com")
PASSWORD = os.environ.get("VERIFY_PASSWORD", "Verify123!@#")


def call(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read().decode()
            return {"status": resp.status, "body": json.loads(payload) if payload else {}, "trace": resp.headers.get("X-Trace-ID")}
    except urllib.error.HTTPError as e:
        payload = e.read().decode()
        return {"status": e.code, "body": json.loads(payload) if payload else {}, "trace": e.headers.get("X-Trace-ID")}


def step(name: str, result: dict, expect: int = 200) -> None:
    ok = result["status"] == expect
    print(f"[{'OK' if ok else 'FAIL'}] {name}: HTTP {result['status']}"
          f"{'' if ok else ' <- 期望 ' + str(expect)}"
          f" trace={result['trace']}")
    if not ok:
        print(f"     body: {result['body']}")
        sys.exit(1)


# 1. 注册（可重复跑，已存在则忽略）
r = call("POST", "/auth/register", {"email": EMAIL, "password": PASSWORD})
if r["status"] not in (200, 201, 400, 409):
    step("注册新用户", r)
elif r["status"] == 400 and "registered" not in json.dumps(r["body"]):
    step("注册新用户", r)
else:
    print(f"[OK] 注册（已存在则继续）: HTTP {r['status']}")

# 2. 登录拿 token
r = call("POST", "/auth/login", {"email": EMAIL, "password": PASSWORD})
step("登录", r)
token = r["body"]["access_token"]
print(f"     access_token 长度: {len(token)}")

# 3. 商品列表，取第一个在售商品
r = call("GET", "/products")
step("商品列表", r)
items = r["body"]
if isinstance(items, dict):
    items = items.get("items", items.get("data", []))
pid = items[0]["id"] if items else None
if not pid:
    print("[FAIL] 无在售商品，先跑 seed 脚本")
    sys.exit(1)
print(f"     选中商品 id: {pid}")

# 4. 加购
r = call("POST", "/cart/items", {"product_id": pid, "quantity": 1}, token=token)
step("加购", r, expect=201)

# 5. 查购物车
r = call("GET", "/cart", token=token)
step("查购物车", r)

# 6. 下单（从购物车生成，无需请求体）
r = call("POST", "/orders", None, token=token)
step("下单", r, expect=201)
order_no = r["body"].get("order_no")
print(f"     订单号: {order_no} 状态: {r['body'].get('status')}")

# 7. 模拟支付
r = call("POST", f"/orders/{order_no}/pay", None, token=token)
step("模拟支付", r)

# 8. 查订单状态
r = call("GET", f"/orders/{order_no}", token=token)
step("查订单详情", r)
print(f"     订单状态: {r['body'].get('status')}")

# 9. 订单列表
r = call("GET", "/orders?size=20", token=token)
step("订单列表", r)
items = r["body"].get("items", [])
print(f"     共 {r['body'].get('total')} 条订单（本页 {len(items)}）")

print("\n=== 业务闭环全部通过 ===")
