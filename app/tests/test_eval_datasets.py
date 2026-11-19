"""评测数据集加载/过滤/校验的纯单测（不依赖外部服务）。"""
from eval import datasets


def test_load_dataset_fills_defaults(monkeypatch):
    monkeypatch.setattr(datasets, "_load_raw", lambda name: [{"question": "q"}])
    cases = datasets.load_dataset("retrieval")
    first = cases[0]
    assert first["id"] == "retrieval-01"
    assert first["tag"] == "general"
    assert first["difficulty"] == "medium"
    assert first["enabled"] is True


def test_filter_cases_by_enabled_tag_difficulty_limit():
    cases = [
        {"id": "a", "enabled": True, "tag": "order", "difficulty": "easy"},
        {"id": "b", "enabled": False, "tag": "order", "difficulty": "easy"},
        {"id": "c", "enabled": True, "tag": "refund", "difficulty": "hard"},
        {"id": "d", "enabled": True, "tag": "order", "difficulty": "hard"},
    ]
    assert [c["id"] for c in datasets.filter_cases(cases)] == ["a", "c", "d"]
    assert [
        c["id"] for c in datasets.filter_cases(cases, tags=["order"])
    ] == ["a", "d"]
    assert [
        c["id"] for c in datasets.filter_cases(cases, difficulty="hard")
    ] == ["c", "d"]
    assert [c["id"] for c in datasets.filter_cases(cases, limit=2)] == ["a", "c"]


def test_fingerprint_is_stable_and_sensitive():
    cases = datasets.load_datasets(["retrieval"])
    assert datasets.fingerprint(cases) == datasets.fingerprint(cases)

    changed = datasets.load_datasets(["retrieval"])
    changed["retrieval"][0]["question"] += "？"
    assert datasets.fingerprint(cases) != datasets.fingerprint(changed)


def test_validate_datasets_strict_passes():
    """标注规范：每条用例都必须带 id/tag/difficulty，且工具在白名单内。"""
    assert datasets.validate_datasets(strict=True) == []


def test_validate_datasets_reports_unknown_tool(monkeypatch):
    original = datasets._load_raw

    def fake_load(name):
        if name == "analytics":
            return [
                {
                    "id": "analytics-99",
                    "tag": "sales",
                    "difficulty": "easy",
                    "question": "q",
                    "expected_tools": ["drop_database"],
                }
            ]
        return original(name)

    monkeypatch.setattr(datasets, "_load_raw", fake_load)
    problems = datasets.validate_datasets(strict=True)
    assert any("白名单外工具" in p for p in problems)
