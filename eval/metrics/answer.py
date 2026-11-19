"""答案质量指标：正确性与忠实度（LLM-as-judge，可插拔）。"""
from __future__ import annotations

from eval.judge import judge_answer


async def judge_metrics(
    question: str,
    answer: str,
    golden: str | None,
    context: list[dict] | None = None,
) -> dict:
    """启用 judge 时计算 correctness/faithfulness。

    无参考答案时指标为 None（不参与聚合），避免用 0 分污染均值。
    """
    if not golden:
        return {
            "correctness": None,
            "faithfulness": None,
            "reason": "",
            "error": None,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    return await judge_answer(question, answer, golden, context=context)
