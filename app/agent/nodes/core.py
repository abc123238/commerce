"""Agent 核心节点：路由、检索、推理、工具执行、答案生成。

所有节点以闭包方式注入 registry/llm，保证单请求内绑定同一会话与用户。
每个节点返回 step 审计记录，供可观测性链路追踪。
"""
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.agent.intent import classify_intent
from app.agent.prompts import ANALYTICS_PROMPT, SYSTEM_PROMPT, build_question_prompt
from app.agent.query_rewrite import rewrite_query
from app.agent.tools.registry import ToolRegistry
from app.core.logging import get_logger
from app.observability.metrics import RETRIEVALS, TOOL_CALLS
from app.rag.citation import build_references, extract_cited_indices, strip_invalid_citations
from app.rag.vectorstore import hybrid_search

logger = get_logger("agent")

_INTENT_LABEL = {"analytics": "经营分析", "customer_service": "客服咨询"}


def _history_messages(history: list[dict] | None) -> list:
    """把对话历史（最近 6 轮）转成消息对象，供两类意图复用。"""
    messages = []
    for turn in (history or [])[-6:]:
        if turn.get("role") == "user":
            messages.append(HumanMessage(content=turn.get("content", "")))
        else:
            messages.append(AIMessage(content=turn.get("content", "")))
    return messages


def make_route_node():
    async def route_node(state: dict) -> dict:
        # intent 由 build_agent 依据问题预分类并写入初始状态（用于提前选定工具集）；
        # 此处兜底再算一次，保证单独调用 route 节点也能得到意图。
        intent = state.get("intent") or classify_intent(state.get("question", ""))
        step = {"node": "route", "detail": f"意图识别：{_INTENT_LABEL.get(intent, intent)}"}
        return {"steps": [step], "intent": intent}

    return route_node


def make_rewrite_node():
    async def rewrite_node(state: dict) -> dict:
        question = state.get("question", "")
        result = await rewrite_query(question, state.get("history", []))
        if result.error:
            detail = f"查询改写失败（{result.error}），使用原问题"
        elif result.query != question:
            detail = f"查询改写：{question[:40]} → {result.query[:60]}"
        else:
            detail = "查询改写：保持原问题"
        return {
            "rewritten_query": result.query,
            "steps": [{"node": "rewrite", "detail": detail}],
        }

    return rewrite_node


def make_retrieve_node():
    async def retrieve_node(state: dict) -> dict:
        question = state.get("question", "")
        # 改写失败时 rewritten_query 已回退为原问题；直接调用节点的场景也能工作。
        query = state.get("rewritten_query") or question
        hits = await hybrid_search(query)
        RETRIEVALS.inc()
        retrieved = [
            {"text": h.text, "source": h.source, "title": h.title} for h in hits
        ]
        # 首次进入时组装消息列表：系统提示 + 历史 + 检索上下文 + 当前问题
        user_prompt = build_question_prompt(question, state.get("history", []), retrieved)
        messages = (
            [SystemMessage(content=SYSTEM_PROMPT)]
            + _history_messages(state.get("history", []))
            + [HumanMessage(content=user_prompt)]
        )
        step = {"node": "retrieve", "detail": f"混合检索召回 {len(retrieved)} 个知识块"}
        return {"retrieved": retrieved, "messages": messages, "steps": [step]}

    return retrieve_node


def make_reason_node(registry: ToolRegistry):
    llm = None

    async def reason_node(state: dict) -> dict:
        nonlocal llm
        from app.agent.llm import get_llm

        if llm is None:
            llm = get_llm().bind_tools(registry.schemas)
        messages = state.get("messages", [])
        if not messages:
            # 未走检索分支（经营分析等）：直接组装系统提示 + 历史 + 问题
            prompt = ANALYTICS_PROMPT if state.get("intent") == "analytics" else SYSTEM_PROMPT
            messages = (
                [SystemMessage(content=prompt)]
                + _history_messages(state.get("history", []))
                + [HumanMessage(content=state.get("question", ""))]
            )
        response = await llm.ainvoke(messages)
        step = {
            "node": "reason",
            "detail": (
                f"调用工具 {len(response.tool_calls)} 个"
                if response.tool_calls
                else "直接作答"
            ),
        }
        return {"messages": [response], "steps": [step]}

    return reason_node


def make_execute_tool_node(registry: ToolRegistry):
    async def execute_tool_node(state: dict) -> dict:
        last = state.get("messages", [])[-1] if state.get("messages") else None
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return {"steps": [{"node": "execute_tool", "detail": "无待执行工具"}]}

        def _signature(name: str, args: dict) -> str:
            return f"{name}:{sorted((str(k), str(v)) for k, v in (args or {}).items())}"

        seen = set(state.get("executed_tools", []))
        results = []
        names: list[str] = []
        processed: list[str] = []
        events: list[dict] = []
        for call in last.tool_calls:
            name, args = call["name"], call.get("args", {})
            sig = _signature(name, args)
            if sig in seen:
                # 相同工具+参数已执行过：不重复执行，提示模型基于已有结果继续，避免空转
                results.append(
                    ToolMessage(
                        content=f"工具 {name} 的相同参数调用已执行过，请基于已有结果继续，不要重复调用",
                        tool_call_id=call["id"],
                        name=name,
                    )
                )
                processed.append(sig)
                names.append(f"{name}(已执行)")
                events.append({"name": name, "args": args, "status": "skipped"})
                continue
            TOOL_CALLS.labels(tool=name).inc()
            result = await registry.execute(name, args)
            results.append(
                ToolMessage(content=result, tool_call_id=call["id"], name=name)
            )
            processed.append(sig)
            names.append(name)
            events.append(
                {
                    "name": name,
                    "args": args,
                    "status": "executed",
                    "result_preview": result[:200],
                }
            )
            logger.info(
                "tool_call",
                run_id=state.get("run_id", ""),
                tool=name,
                args=args,
                result_preview=result[:100],
            )
        return {
            "messages": results,
            "executed_tools": processed,
            "tool_events": events,
            "steps": [{"node": "execute_tool", "detail": f"执行 {len(results)} 个工具：{', '.join(names)}"}],
        }

    return execute_tool_node


def make_answer_node():
    async def answer_node(state: dict) -> dict:
        from app.agent.llm import extract_text

        last = state.get("messages", [])[-1] if state.get("messages") else None
        # content 可能是 str（普通模型）或块列表（thinking 模型），统一用 extract_text 提取
        text = extract_text(last.content) if isinstance(last, AIMessage) else ""
        if not text.strip():
            text = "抱歉，我暂时无法生成有效回答。您可以换个问法，或提供更多信息。"
        # 引用校验：抽取 [n] 映射到检索资料，并去掉越界的悬空引用标记
        retrieved = state.get("retrieved", [])
        text = strip_invalid_citations(text, max_index=len(retrieved))
        cited = extract_cited_indices(text, max_index=len(retrieved))
        citations = [
            {
                "index": idx,
                "text": retrieved[idx - 1].get("text", ""),
                "source": retrieved[idx - 1].get("source", ""),
                "title": retrieved[idx - 1].get("title", ""),
            }
            for idx in cited
        ]
        final = f"{text}\n\n{build_references(citations)}" if citations else text
        step = {"node": "answer", "detail": f"引用 {len(citations)} 条"}
        return {"answer": final, "citations": citations, "steps": [step]}

    return answer_node
