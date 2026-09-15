"""LangGraph 图：route -> (retrieve | reason) -> reason <-> execute_tool 循环 -> answer。

单 Agent 多步骤；route 按意图分流：客服走 RAG 检索，经营分析直接走工具推理。
每个节点产出 step 审计记录，配合 Langfuse 回调实现可观测链路。
"""
from langgraph.graph import END, START, StateGraph

from app.agent.intent import classify_intent
from app.observability.metrics import AGENT_RUNS
from app.agent.nodes.core import (
    make_answer_node,
    make_execute_tool_node,
    make_reason_node,
    make_retrieve_node,
    make_route_node,
)
from app.agent.state import AgentState
from app.agent.tools.registry import ToolRegistry

_MAX_STEPS = 6


def _route_after_route(state: dict) -> str:
    """意图分流：经营分析跳过 RAG，直接进入工具推理。"""
    if state.get("intent") == "analytics":
        return "reason"
    return "retrieve"


def _route_after_reason(state: dict) -> str:
    last = state.get("messages", [])[-1] if state.get("messages") else None
    steps = state.get("steps", [])
    tool_runs = sum(
        1
        for s in steps
        if s.get("node") == "execute_tool" and s.get("detail") != "无待执行工具"
    )
    has_calls = bool(getattr(last, "tool_calls", None))
    if has_calls and tool_runs < state.get("max_steps", _MAX_STEPS):
        return "execute_tool"
    return "answer"  # 无工具调用或已超步数上限，强制收束


def build_graph(registry: ToolRegistry):
    """构建状态图：registry 已绑定当前会话与用户。"""
    graph = StateGraph(AgentState)
    graph.add_node("route", make_route_node())
    graph.add_node("retrieve", make_retrieve_node())
    graph.add_node("reason", make_reason_node(registry))
    graph.add_node("execute_tool", make_execute_tool_node(registry))
    graph.add_node("answer", make_answer_node())

    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route",
        _route_after_route,
        {"retrieve": "retrieve", "reason": "reason"},
    )
    graph.add_edge("retrieve", "reason")
    graph.add_conditional_edges(
        "reason",
        _route_after_reason,
        {"execute_tool": "execute_tool", "answer": "answer"},
    )
    graph.add_edge("execute_tool", "reason")
    graph.add_edge("answer", END)
    return graph.compile()


def build_agent(
    db,
    user_id: int,
    question: str,
    history: list[dict] | None = None,
    run_id: str | None = None,
    callbacks: list | None = None,
    intent: str | None = None,
    user_role: str | None = None,
):
    """按意图组装工具集 -> 构建图，返回 (graph, initial_state, config)。

    intent 缺省时由 classify_intent 自动识别：客服走订单/商品工具 + RAG，
    经营分析走销售聚合/增长/畅销榜工具（不检索知识库）。
    callbacks 由可观测层注入（Langfuse 等）。
    """
    from app.agent.tools.analytics_tools import build_analytics_tools
    from app.agent.tools.order_tools import build_order_tools
    from app.agent.tools.product_tools import build_product_tools

    intent = intent or classify_intent(question)
    # 越权防护：经营分析工具聚合的是「全部用户」的销售数据，仅管理员可用。
    # 普通用户即使问题命中分析意图，也回退到客服工具集（订单/商品均按 user_id 隔离）。
    if intent == "analytics" and user_role != "admin":
        intent = "customer_service"
    AGENT_RUNS.labels(intent=intent).inc()
    if intent == "analytics":
        tools = build_analytics_tools(db)
    else:
        tools = build_order_tools(db, user_id) + build_product_tools(db)

    registry = ToolRegistry(tools)
    graph = build_graph(registry)
    initial = {
        "user_id": user_id,
        "question": question,
        "history": history or [],
        "intent": intent,
        "max_steps": _MAX_STEPS,
        "run_id": run_id or "",
    }
    config = {"callbacks": callbacks or []}
    return graph, initial, config


async def run_agent(
    db,
    user_id: int,
    question: str,
    history: list[dict] | None = None,
    run_id: str | None = None,
    callbacks: list | None = None,
    user_role: str | None = None,
) -> dict:
    """统一非流式入口：返回 {answer, citations, steps}。"""
    graph, initial, config = build_agent(
        db, user_id, question, history, run_id, callbacks, user_role=user_role
    )
    result = await graph.ainvoke(initial, config=config)
    return {
        "answer": result.get("answer", ""),
        "citations": result.get("citations", []),
        "steps": result.get("steps", []),
        "retrieved": result.get("retrieved", []),
        "intent": result.get("intent", ""),
    }
