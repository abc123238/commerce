"""答案质量指标：正确性与忠实度（LLM-as-judge，可插拔）。"""
from __future__ import annotations

from eval.judge import judge_answer


async def judge_metrics(question: str, answer: str, golden: str | None) -> dict:
    """启用 judge 时计算 correctness/faithfulness；无参考答案则返回 0。"""
    if not golden:
        return {"correctness": 0.0, "faithfulness": 0.0}
    return await judge_answer(question, answer, golden)
