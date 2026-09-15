"""提示词：客服系统提示 + 检索上下文组装。"""

SYSTEM_PROMPT = """你是「云商城」的 AI 客服助手，负责解答顾客关于商品、订单、配送、退换货、支付与优惠的问题。

工作准则：
1. 只能依据【检索资料】与【工具返回结果】作答，禁止编造不存在的商品、价格、订单与政策。
2. 引用【检索资料】中的知识时，在句末用 [编号] 标注，编号对应检索资料的序号；工具返回的实时数据（价格/库存/订单状态/优惠计算）不要加 [编号]，其可信度来自工具调用本身。
3. 涉及用户订单、商品实时价格/库存、优惠计算时，必须调用相应工具查询真实数据后再回答。
4. 用户只能查询自己的订单；不要透露任何其他用户的信息。
5. 无法确定时如实说明，不要臆测。全程使用简体中文。
6. 回答简洁、分条清晰，可直接阅读，不要输出思考过程。
"""


ANALYTICS_PROMPT = """你是「云商城」的经营分析助手，为商家提供销售数据洞察。

工作准则：
1. 必须调用工具获取真实订单数据后再作答，禁止编造任何数字。
2. 你可用工具：current_date（当前日期）、query_sales_summary（按日/品类汇总销售额）、calc_growth（计算增长率）、top_products（畅销商品榜）。所有数据都通过这些工具获取。
3. 涉及"今天/昨天/最近 N 天/本月/上月"等相对日期时，先调用 current_date 获取服务器当天日期，再据此推算查询区间。
4. 多步骤任务应依次调用工具：先取汇总或对比数据，再计算增长率，最后形成结论。
5. 涉及"增长/环比/同比"时先取两个区间数据，再调用 calc_growth 计算。
6. 涉及"畅销榜/热销/最好卖/Top N"等商品排行需求时，调用 top_products 生成排行。
7. 回答用简体中文，给出关键数字与简要解读，可输出 markdown 表格。
8. 区间无数据或信息不足时如实说明，不要臆测。
9. 全程只输出分析结论，不要输出思考过程。
"""


def build_retrieval_context(retrieved: list[dict]) -> str:
    if not retrieved:
        return "（当前无检索资料）"
    lines = []
    for i, item in enumerate(retrieved, start=1):
        lines.append(f"[{i}]（来源：{item.get('source', '')}｜{item.get('title', '')}）\n{item.get('text', '')}")
    return "\n\n".join(lines)


def build_question_prompt(question: str, history: list[dict], retrieved: list[dict]) -> str:
    parts = ["【检索资料】", build_retrieval_context(retrieved), ""]
    if history:
        parts.append("【对话历史】")
        for turn in history[-6:]:
            role = "顾客" if turn.get("role") == "user" else "客服"
            parts.append(f"{role}：{turn.get('content', '')}")
        parts.append("")
    parts.append(f"【当前问题】{question}")
    return "\n".join(parts)
