"""LangGraph 状态定义：单 Agent 多步骤，携带审计步骤与引用。"""
import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage


class AgentState(TypedDict, total=False):
    user_id: int                      # 当前登录用户（工具权限边界）
    question: str                     # 本轮用户问题
    rewritten_query: str              # 结合历史改写后的检索 query（失败时为原问题）
    history: list[dict]               # 前几轮对话 [{role, content}]
    retrieved: list[dict]             # RAG 检索结果 [{text, source, title}]
    citations: list[dict]             # 最终引用 [{index, text, source, title}]
    messages: Annotated[list[BaseMessage], operator.add]  # 模型往返消息
    steps: Annotated[list[dict], operator.add]            # 审计步骤（工具调用轨迹）
    tool_events: Annotated[list[dict], operator.add]      # 结构化工具事件（评测用：名称/参数/结果预览）
    executed_tools: Annotated[list[str], operator.add]    # 已执行工具签名（防重复空转）
    max_steps: int                    # 工具调用上限
    answer: str                       # 最终回答
    run_id: str                       # 本次 agent 运行 id（对应 HTTP trace_id）
