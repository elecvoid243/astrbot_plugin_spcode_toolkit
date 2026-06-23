"""测试 tools/_stats.py 的 _stats dict + _record() 函数。"""
import time
from tools import _stats
from tools._stats import _record, get_stats, reset


def test_record_increments_count():
    """连续 _record() 应递增 count。"""
    reset()
    _record("tool_a")
    _record("tool_a")
    stats = get_stats()
    assert stats["tool_a"]["count"] == 2


def test_record_updates_last_timestamp():
    """_record() 应更新时间戳到 time.time() 调用时刻。"""
    reset()
    before = time.time()
    _record("tool_b")
    after = time.time()
    stats = get_stats()
    assert before <= stats["tool_b"]["last"] <= after


def test_record_handles_unknown_name():
    """首次调用 _record() 新名字应自动创建 entry(count=1, last=now)。"""
    reset()
    _record("new_tool")
    entry = get_stats()["new_tool"]
    assert entry["count"] == 1
    assert entry["last"] > 0


def test_record_swallows_exceptions(monkeypatch):
    """_record 内部 try/except 兜底,模拟 _stats 损坏时不抛异常。"""
    class BrokenStats:
        def __getitem__(self, key):
            raise RuntimeError("stats broken")

    monkeypatch.setattr(_stats, "_stats", BrokenStats())
    # 不应抛异常(内部 try/except 吞掉)
    try:
        _record("tool_c")
    except RuntimeError as e:
        raise AssertionError(
            f"_record 应吞掉异常,但抛出了: {e}"
        )


def test_get_stats_returns_shallow_copy():
    """get_stats() 必须返回浅拷贝(防止外部 mutate 内部状态)。"""
    reset()
    _record("tool_d")
    snapshot = get_stats()
    snapshot["tool_d"]["count"] = 99999
    # 内部状态不应被影响
    assert get_stats()["tool_d"]["count"] == 1


def test_reset_clears_all_state():
    """reset() 后所有计数应清空。"""
    _record("x")
    _record("y")
    reset()
    assert get_stats() == {}
