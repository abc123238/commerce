"""LLM token 用量采集与成本估算。

LangChain 的 usage 信息可能出现在两处：消息的 usage_metadata（新版模型适配层）
或 LLMResult.llm_output["token_usage"]（旧版 / 部分网关）。这里统一兜底读取，
避免不同 provider 之间口径漂移。
"""
from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler

from eval import config

_COUNT_KEYS = {
    "input": ("input_tokens", "prompt_tokens"),
    "output": ("output_tokens", "completion_tokens"),
}


def _read_usage(usage: dict | None) -> tuple[int, int]:
    if not isinstance(usage, dict):
        return 0, 0
    input_tokens = next((usage[k] for k in _COUNT_KEYS["input"] if usage.get(k) is not None), 0)
    output_tokens = next((usage[k] for k in _COUNT_KEYS["output"] if usage.get(k) is not None), 0)
    return int(input_tokens or 0), int(output_tokens or 0)


def usage_from_message(message) -> tuple[int, int]:
    """从 AIMessage 读取 (input_tokens, output_tokens)。"""
    usage = getattr(message, "usage_metadata", None)
    if usage:
        input_tokens, output_tokens = _read_usage(usage)
        if input_tokens or output_tokens:
            return input_tokens, output_tokens
    metadata = getattr(message, "response_metadata", None) or {}
    for key in ("usage", "token_usage"):
        input_tokens, output_tokens = _read_usage(metadata.get(key))
        if input_tokens or output_tokens:
            return input_tokens, output_tokens
    return 0, 0


def _usage_from_llm_result(response) -> tuple[int, int]:
    for generation_list in getattr(response, "generations", []) or []:
        for generation in generation_list:
            message = getattr(generation, "message", None)
            if message is not None:
                input_tokens, output_tokens = usage_from_message(message)
                if input_tokens or output_tokens:
                    return input_tokens, output_tokens
    llm_output = getattr(response, "llm_output", None) or {}
    for key in ("token_usage", "usage"):
        input_tokens, output_tokens = _read_usage(llm_output.get(key))
        if input_tokens or output_tokens:
            return input_tokens, output_tokens
    return 0, 0


class UsageCollector(BaseCallbackHandler):
    """挂在 LangGraph 调用上的回调：累计一次 agent 运行内所有 LLM 调用的用量。"""

    def __init__(self) -> None:
        super().__init__()
        self.input_tokens = 0
        self.output_tokens = 0
        self.llm_calls = 0

    def on_llm_end(self, response, **kwargs) -> None:  # noqa: ANN001
        self.llm_calls += 1
        input_tokens, output_tokens = _usage_from_llm_result(response)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """按配置单价估算美元成本（保留 6 位，避免单条太小被抹成 0）。"""
    return round(
        input_tokens / 1_000_000 * config.INPUT_PRICE_PER_MTOK_USD
        + output_tokens / 1_000_000 * config.OUTPUT_PRICE_PER_MTOK_USD,
        6,
    )
