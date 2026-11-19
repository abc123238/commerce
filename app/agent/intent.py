"""意图分类：将用户问题分流到 客服咨询(customer_service) 或 经营分析(analytics)。

纯函数、无 IO，便于单测；词频启发式即可满足演示场景，
复杂问法在 reason 节点由 LLM 继续判断是否调用分析工具。
"""
_ANALYTICS_KEYWORDS = [
    "销售", "业绩", "营收", "收入", "利润", "毛利", "销量", "统计", "报表",
    "增长", "环比", "同比", "畅销", "最好卖", "卖得最好", "卖得好", "热销",
    "排行榜", "榜单", "排名", "走势", "汇总", "经营", "总额", "日均",
    "复购", "客单价", "趋势", "销售额", "数据", "月度", "季度",
    "卖了多少", "平均每天", "总订单",
]

_CUSTOMER_KEYWORDS = [
    "订单", "物流", "快递", "发货", "退", "换货", "售后", "保修", "库存",
    "价格", "多少钱", "优惠", "折扣", "优惠券", "支付", "客服", "商品",
    "规格", "参数", "怎么买", "送货", "到货", "怎么用",
]


def classify_intent(question: str) -> str:
    """返回 "analytics"（经营分析）或 "customer_service"（客服咨询）。

    按关键词加权：分析词 +1，客服词 -1；得分 >0 判为分析，否则客服。
    """
    q = question or ""
    analytics_hits = sum(1 for kw in _ANALYTICS_KEYWORDS if kw in q)
    customer_hits = sum(1 for kw in _CUSTOMER_KEYWORDS if kw in q)
    # 有分析信号时平局也判分析：「今天总订单数是多少」同时命中「总订单」「订单」，
    # 但它问的是经营数据而非某个订单；完全没有分析信号时才归客服。
    if analytics_hits and analytics_hits - customer_hits >= 0:
        return "analytics"
    return "customer_service"
