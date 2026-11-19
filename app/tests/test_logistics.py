"""模拟物流轨迹的确定性纯单测（无需 DB / Redis）。"""
from datetime import datetime, timedelta, timezone

from app.services.logistics_service import (
    _STAGES,
    build_tracking_nodes,
    current_stage_index,
)

STAGE = 120
PAID_AT = datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)


def _reached_count(elapsed_seconds: int) -> int:
    now = PAID_AT + timedelta(seconds=elapsed_seconds)
    return sum(n.reached for n in build_tracking_nodes(PAID_AT, now, STAGE))


def test_stage_boundaries():
    assert _reached_count(0) == 1  # 刚支付：只有「等待发货」
    assert _reached_count(119) == 1  # 差 1 秒不跨阶段
    assert _reached_count(120) == 2  # 恰好跨过揽收
    assert _reached_count(240) == 3
    assert _reached_count(600) == 6  # 10 分钟走完全程
    assert _reached_count(6000) == 6  # 再久也不会多出来


def test_nodes_are_monotonic():
    """时间只前进，已到达的节点不会回退。"""
    earlier = build_tracking_nodes(PAID_AT, PAID_AT + timedelta(seconds=300), STAGE)
    later = build_tracking_nodes(PAID_AT, PAID_AT + timedelta(seconds=600), STAGE)
    reached_earlier = {n.name for n in earlier if n.reached}
    reached_later = {n.name for n in later if n.reached}
    assert reached_earlier <= reached_later


def test_node_time_is_exact_offset():
    """节点时刻 = paid_at + 下标 × 阶段间隔，与查询时刻无关（可复现的根据）。"""
    nodes = build_tracking_nodes(PAID_AT, PAID_AT + timedelta(seconds=999), STAGE)
    assert [n.time for n in nodes] == [
        PAID_AT + timedelta(seconds=i * STAGE) for i in range(len(_STAGES))
    ]
    assert nodes[3].time == PAID_AT + timedelta(seconds=360)


def test_same_moment_is_reproducible():
    """同一 paid_at + 同一 now 两次调用结果完全一致（无随机）。"""
    now = PAID_AT + timedelta(seconds=250)
    assert build_tracking_nodes(PAID_AT, now, STAGE) == build_tracking_nodes(
        PAID_AT, now, STAGE
    )


def test_custom_stage_seconds():
    """阶段间隔可配置：60 秒一阶段时，60 秒即跨过揽收。"""
    now = PAID_AT + timedelta(seconds=60)
    assert sum(n.reached for n in build_tracking_nodes(PAID_AT, now, 60)) == 2
    assert sum(n.reached for n in build_tracking_nodes(PAID_AT, now, 120)) == 1


def test_clock_skew_clamps_to_zero():
    """now 早于 paid_at（时钟漂移）不能产生负进度，也不抛异常。"""
    nodes = build_tracking_nodes(PAID_AT, PAID_AT - timedelta(hours=1), STAGE)
    assert sum(n.reached for n in nodes) == 1
    assert nodes[0].time == PAID_AT


def test_naive_paid_at_treated_as_utc():
    """历史数据里的 naive datetime 按 UTC 解释，不崩也不偏。"""
    naive = PAID_AT.replace(tzinfo=None)
    nodes = build_tracking_nodes(naive, PAID_AT + timedelta(seconds=120), STAGE)
    assert sum(n.reached for n in nodes) == 2
    assert nodes[0].time.tzinfo is not None


def test_current_stage_index():
    assert current_stage_index(PAID_AT, PAID_AT, STAGE) == 0
    assert current_stage_index(PAID_AT, PAID_AT + timedelta(seconds=120), STAGE) == 1
    assert current_stage_index(PAID_AT, PAID_AT + timedelta(seconds=600), STAGE) == 5
    # 超出全程封顶在最后一个阶段，不越界
    assert current_stage_index(PAID_AT, PAID_AT + timedelta(days=30), STAGE) == 5
