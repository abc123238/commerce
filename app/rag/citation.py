"""引用提取与校验：从回答文本中抽取 [n] 引用并映射到检索资料。"""
import re

# 排除 `[n](`（markdown 链接语法），避免把链接文案/URL 中的方括号数字误判为引用
_PATTERN = re.compile(r"\[(\d+)\](?!\()")


def extract_cited_indices(text: str, max_index: int | None = None) -> list[int]:
    """抽取文本中的 [n] 引用序号，去重排序，过滤越界引用。"""
    indices = {int(m) for m in _PATTERN.findall(text)}
    if max_index is not None:
        indices = {i for i in indices if 1 <= i <= max_index}
    return sorted(indices)


def strip_invalid_citations(text: str, max_index: int) -> str:
    """去掉文本中越界的 [n] 引用标记（无对应检索资料的悬空引用）。"""
    return _PATTERN.sub(
        lambda m: m.group(0) if 1 <= int(m.group(1)) <= max_index else "", text
    )


def build_references(citations: list[dict]) -> str:
    """将引用条目格式化为「参考资料」段落。"""
    if not citations:
        return ""
    refs = "\n".join(
        f"[{c['index']}] {c['title']}（{c['source']}）" for c in citations
    )
    return f"参考资料：\n{refs}"
