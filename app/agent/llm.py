"""LLM 薄适配层：隔离 langchain-anthropic 的 API 动荡，统一模型工厂。"""
from functools import lru_cache

from app.config import settings

MAX_TOKENS = 2048


def _llm_kwargs(max_tokens: int = MAX_TOKENS) -> dict:
    return {
        "model": settings.anthropic_model,
        "base_url": settings.anthropic_base_url,
        "api_key": settings.anthropic_auth_token,
        "max_tokens": max_tokens,
        "temperature": 0,
        "timeout": settings.llm_timeout_seconds,  # 上游卡住时抛超时，避免 SSE 无限挂起
    }


@lru_cache(maxsize=1)
def _cached_llm():
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(**_llm_kwargs())


def get_llm():
    """返回带缓存的 ChatAnthropic 实例（工具绑定在调用侧完成）。"""
    if not settings.anthropic_base_url or not settings.anthropic_auth_token:
        raise RuntimeError("未配置 ANTHROPIC_BASE_URL / AUTH_TOKEN，无法调用 LLM")
    return _cached_llm()


@lru_cache(maxsize=1)
def _cached_judge_llm():
    from langchain_anthropic import ChatAnthropic

    # 评测裁判直接禁用 thinking：reasoning 模型的思考块可能耗尽 token 预算
    # 导致只有 thinking 而没有 text，使打分解析失败。裁判任务不需要推理链。
    return ChatAnthropic(**_llm_kwargs(max_tokens=4096), thinking={"type": "disabled"})


def get_judge_llm():
    """评测裁判专用 LLM：更大 max_tokens，保证思考后仍能输出打分 JSON。"""
    if not settings.anthropic_base_url or not settings.anthropic_auth_token:
        raise RuntimeError("未配置 ANTHROPIC_BASE_URL / AUTH_TOKEN，无法调用 LLM")
    return _cached_judge_llm()


def extract_text(content) -> str:
    """从 AIMessage.content 中提取文本块（兼容 reasoning 模型返回的 thinking 块）。"""
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, dict) and block.get("type") == "thinking":
            continue
    return "".join(parts).strip()
