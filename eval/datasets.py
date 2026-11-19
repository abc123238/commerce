"""评测数据集的加载、过滤、指纹与校验。

JSONL 是唯一事实来源：可 review、可离线校验、随 git 版本化；
数据库只保存执行时的快照，不反过来作为配置源。
"""
from __future__ import annotations

import hashlib
import json

from eval import config

DATASETS = ("retrieval", "customer_service", "analytics", "robustness")

# 各数据集允许出现的工具名，防止标注笔误导致工具序列指标静默为 0。
TOOL_ALLOWLIST: dict[str, set[str]] = {
    "retrieval": set(),
    "customer_service": {
        "query_order",
        "query_tracking",
        "query_product",
        "calc_discount",
        "prepare_order",
        "confirm_order",
    },
    "analytics": {
        "current_date",
        "query_sales_summary",
        "calc_growth",
        "top_products",
    },
    "robustness": {
        "query_order",
        "query_tracking",
        "query_product",
        "calc_discount",
        "prepare_order",
        "confirm_order",
    },
}

_REQUIRED_FIELDS = {
    "retrieval": ("question", "golden_doc_ids"),
    "customer_service": ("question", "expected_tools"),
    "analytics": ("question", "expected_tools"),
    "robustness": ("question", "expected_tools", "expected_behavior"),
}

_DIFFICULTIES = {"easy", "medium", "hard"}


def _load_raw(name: str) -> list[dict]:
    if name not in DATASETS:
        raise ValueError(f"未知数据集：{name}")
    path = config.DATASETS_DIR / f"{name}.jsonl"
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))
    return cases


def _normalize(name: str, raw: dict, index: int) -> dict:
    case = dict(raw)
    case.setdefault("id", f"{name}-{index:02d}")
    case.setdefault("tag", "general")
    case.setdefault("difficulty", "medium")
    case.setdefault("enabled", True)
    case.setdefault("user_role", "admin" if name == "analytics" else "customer")
    return case


def load_dataset(name: str) -> list[dict]:
    return [_normalize(name, raw, i) for i, raw in enumerate(_load_raw(name), 1)]


def filter_cases(
    cases: list[dict],
    *,
    tags: list[str] | None = None,
    difficulty: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    selected = [c for c in cases if c.get("enabled", True)]
    if tags:
        wanted = set(tags)
        selected = [c for c in selected if c.get("tag") in wanted]
    if difficulty:
        selected = [c for c in selected if c.get("difficulty") == difficulty]
    if limit is not None:
        selected = selected[:limit]
    return selected


def load_datasets(
    names: list[str] | None = None,
    *,
    tags: list[str] | None = None,
    difficulty: str | None = None,
    limit: int | None = None,
) -> dict[str, list[dict]]:
    names = names or list(DATASETS)
    return {
        name: filter_cases(
            load_dataset(name), tags=tags, difficulty=difficulty, limit=limit
        )
        for name in names
    }


def fingerprint(cases_by_dataset: dict[str, list[dict]]) -> str:
    payload = json.dumps(
        cases_by_dataset,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_datasets(*, strict: bool = False) -> list[str]:
    """返回问题列表；空列表表示通过。

    strict=True 时额外要求 id/tag/difficulty 显式写在 JSONL 里，
    用于 CI 守护标注规范；默认宽松以兼容历史数据集。
    """
    problems: list[str] = []
    for name in DATASETS:
        seen_ids: set[str] = set()
        raws = _load_raw(name)
        for index, raw in enumerate(raws, 1):
            case_id = raw.get("id", f"{name}-{index:02d}")
            if case_id in seen_ids:
                problems.append(f"{name}: 重复用例 id {case_id}")
            seen_ids.add(case_id)

            for field in _REQUIRED_FIELDS[name]:
                if field not in raw:
                    problems.append(f"{name}:{case_id} 缺少必需字段 {field}")

            expected_tools = raw.get("expected_tools") or []
            unknown = set(expected_tools) - TOOL_ALLOWLIST[name]
            if unknown:
                problems.append(
                    f"{name}:{case_id} 出现白名单外工具 {sorted(unknown)}"
                )

            if name == "retrieval":
                for doc_id in raw.get("golden_doc_ids", []):
                    if not isinstance(doc_id, str) or ":" not in doc_id:
                        problems.append(
                            f"{name}:{case_id} golden_doc_id 格式应为 '来源:章节'：{doc_id!r}"
                        )
                history = raw.get("history")
                if history is not None:
                    if not isinstance(history, list):
                        problems.append(f"{name}:{case_id} history 应为列表")
                    else:
                        for turn in history:
                            if (
                                not isinstance(turn, dict)
                                or turn.get("role") not in ("user", "assistant")
                                or not isinstance(turn.get("content"), str)
                            ):
                                problems.append(
                                    f"{name}:{case_id} history 元素应为 "
                                    "{role: user|assistant, content: str}"
                                )
                                break

            if strict:
                for field in ("id", "tag", "difficulty"):
                    if field not in raw:
                        problems.append(f"{name}:{case_id} 缺少标注字段 {field}")
                difficulty = raw.get("difficulty")
                if difficulty is not None and difficulty not in _DIFFICULTIES:
                    problems.append(
                        f"{name}:{case_id} difficulty 非法：{difficulty!r}"
                    )
    return problems
