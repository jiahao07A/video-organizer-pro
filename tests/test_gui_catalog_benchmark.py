import pytest

from gui.main_window import MainWindow
from tools import gui_performance_benchmark as benchmark


def test_small_benchmark_snapshots_rows_before_filtering(app):
    result = benchmark.run(app, 50)
    assert result["first_page_rows"] == 50
    assert result["filtered_rows"] == 0


def test_ignored_search_condition_is_not_reported_as_a_success(app, monkeypatch):
    from gui.views.material_library import MaterialLibraryView

    monkeypatch.setattr(MaterialLibraryView, "_query_params", lambda self: {})
    with pytest.raises(RuntimeError, match="检索结果不符"):
        benchmark.run(app, 1250)  # 只有 clip-0001000 到 clip-0001249 匹配


def test_query_timeout_is_a_failure_and_closes_the_window(app, monkeypatch):
    original_wait = benchmark.pump_until
    calls = 0
    previous_windows = set(app.topLevelWidgets())

    def limit_second_wait(application, predicate):
        nonlocal calls
        calls += 1
        return original_wait(application, predicate, timeout=0 if calls == 2 else 5)

    monkeypatch.setattr(benchmark, "pump_until", limit_second_wait)
    with pytest.raises(TimeoutError, match="防抖"):
        benchmark.run(app, 50)
    assert calls == 2
    assert not [window for window in app.topLevelWidgets() if isinstance(window, MainWindow)
                and window not in previous_windows]
