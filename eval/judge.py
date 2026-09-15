"""LLM-as-judge：评估 AI 回答的正确性与忠实度（0~1）。

复用 agent 的 LLM 适配层；失败时返回 0 分并记录原因，不让评测整体中断。
"""
import json
import re

from app.agent.llm import extract_text, get_judge_llm
from app.core.logging import get_logger

logger = get_logger("eval")

_JUDGE_PROMPT = """你是严谨的评测裁判。请给 AI 助手对用户问题的回答打分（0~1，一位小数）。

【问题】{question}
【参考答案】{golden}
【AI 回答】{answer}

评分维度：
- correctness 正确性：回答是否准确、完整地覆盖问题要点，与参考答案信息一致（不要求逐字）。
- faithfulness 忠实度：回答中的数字、事实是否无编造、无幻觉，是否与给定上下文一致。

只输出一行 JSON（不要任何其他文字）：
{{"correctness": 0.0, "faithfulness": 0.0, "reason": "一句话理由"}}"""


def _strip_code_fence(text: str) -> str:
    """去掉裁判输出外层可能包裹的 ```json ... ``` 代码块。"""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()).strip()


async def judge_answer(question: str, answer: str, golden: str) -> dict:
    """返回 {"correctness", "faithfulness", "reason"}。"""
    if not answer.strip():
        return {"correctness": 0.0, "faithfulness": 0.0, "reason": "回答为空"}
    llm = get_judge_llm()
    resp = await llm.ainvoke(_JUDGE_PROMPT.format(question=question, golden=golden, answer=answer))
    text = _strip_code_fence(extract_text(resp.content))
    # 兜底重试一次（极端情况下的瞬时抖动）。
    if not text:
        logger.warning("judge_empty_retry")
        resp = await llm.ainvoke(_JUDGE_PROMPT.format(question=question, golden=golden, answer=answer))
        text = _strip_code_fence(extract_text(resp.content))
    try:
        payload = json.loads(text)
        return {
            "correctness": float(payload.get("correctness", 0.0)),
            "faithfulness": float(payload.get("faithfulness", 0.0)),
            "reason": str(payload.get("reason", ""))[:120],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("judge_parse_fail", exc=str(exc)[:100], raw=text[:120])
        return {"correctness": 0.0, "faithfulness": 0.0, "reason": "裁判输出无法解析"}
