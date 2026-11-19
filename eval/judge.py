"""LLM-as-judge：评估 AI 回答的正确性与忠实度（0~1）。

复用 agent 的 LLM 适配层；调用或解析失败时返回 null 指标并记录 error，
以便把「运行失败」与「真 0 分」区分开，而不是静默填 0 拉低均值。
"""
import json
import re

from app.agent.llm import extract_text, get_judge_llm
from app.core.logging import get_logger
from eval.usage import usage_from_message

logger = get_logger("eval")

_JUDGE_PROMPT = """你是严谨的评测裁判。请给 AI 助手对用户问题的回答打分（0~1，一位小数）。

【问题】{question}
【参考答案】{golden}
{context}
【AI 回答】{answer}

评分维度：
- correctness 正确性：回答是否准确、完整地覆盖问题要点，与参考答案信息一致（不要求逐字）。
- faithfulness 忠实度：回答中的数字、事实是否无编造、无幻觉，是否与参考资料/给定上下文一致。

只输出一行 JSON（不要任何其他文字）：
{{"correctness": 0.0, "faithfulness": 0.0, "reason": "一句话理由"}}"""

_MAX_CONTEXT_DOCS = 5
_MAX_CONTEXT_CHARS = 300


def _format_context(context: list[dict] | None) -> str:
    """把检索证据渲染进裁判提示词；无可用证据时返回空串。"""
    blocks = []
    for index, item in enumerate((context or [])[:_MAX_CONTEXT_DOCS], 1):
        text = (item.get("text") or "").strip()
        if not text:
            continue
        title = item.get("title") or item.get("source") or "资料"
        source = item.get("source") or ""
        blocks.append(f"[{index}] {title}（{source}）\n{text[:_MAX_CONTEXT_CHARS]}")
    if not blocks:
        return ""
    return "【可验证参考资料】\n" + "\n\n".join(blocks) + "\n"


def _strip_code_fence(text: str) -> str:
    """去掉裁判输出外层可能包裹的 ```json ... ``` 代码块。"""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()).strip()


async def judge_answer(
    question: str, answer: str, golden: str, context: list[dict] | None = None
) -> dict:
    """返回 {"correctness", "faithfulness", "reason", "error"}。

    error 非空表示裁判本身失败（调用/解析），此时指标为 None，不计入均值。
    """
    if not answer.strip():
        return {
            "correctness": 0.0,
            "faithfulness": 0.0,
            "reason": "回答为空",
            "error": None,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    prompt = _JUDGE_PROMPT.format(
        question=question,
        golden=golden,
        context=_format_context(context),
        answer=answer,
    )
    input_tokens = output_tokens = 0
    try:
        llm = get_judge_llm()
        resp = await llm.ainvoke(prompt)
        usage = usage_from_message(resp)
        input_tokens += usage[0]
        output_tokens += usage[1]
        text = _strip_code_fence(extract_text(resp.content))
        # 兜底重试一次（极端情况下的瞬时抖动）。
        if not text:
            logger.warning("judge_empty_retry")
            resp = await llm.ainvoke(prompt)
            usage = usage_from_message(resp)
            input_tokens += usage[0]
            output_tokens += usage[1]
            text = _strip_code_fence(extract_text(resp.content))
    except Exception as exc:  # noqa: BLE001
        logger.warning("judge_call_fail", exc=str(exc)[:120])
        return {
            "correctness": None,
            "faithfulness": None,
            "reason": "",
            "error": f"judge_call_failed: {str(exc)[:120]}",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    try:
        payload = json.loads(text)
        return {
            "correctness": float(payload.get("correctness", 0.0)),
            "faithfulness": float(payload.get("faithfulness", 0.0)),
            "reason": str(payload.get("reason", ""))[:120],
            "error": None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("judge_parse_fail", exc=str(exc)[:100], raw=text[:120])
        return {
            "correctness": None,
            "faithfulness": None,
            "reason": "裁判输出无法解析",
            "error": "judge_parse_failed",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
