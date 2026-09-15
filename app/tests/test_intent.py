"""意图分类纯单测。"""
from app.agent.intent import classify_intent


def test_analytics_questions():
    assert classify_intent("帮我统计上个月销售额和订单数") == "analytics"
    assert classify_intent("对比本月和上月的营收增长") == "analytics"
    assert classify_intent("哪些商品最好卖，出个畅销榜") == "analytics"
    assert classify_intent("哪个商品卖得最好，出个畅销榜") == "analytics"  # "卖得最好" 词序回归
    assert classify_intent("最近七天的日均销量趋势怎么样") == "analytics"


def test_customer_service_questions():
    assert classify_intent("我的订单发货了吗") == "customer_service"
    assert classify_intent("无线蓝牙耳机多少钱") == "customer_service"
    assert classify_intent("怎么退货") == "customer_service"
    assert classify_intent("这款商品的库存和价格是多少") == "customer_service"


def test_empty_and_plain():
    assert classify_intent("") == "customer_service"
    assert classify_intent("你好") == "customer_service"
