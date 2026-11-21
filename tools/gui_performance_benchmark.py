# -*- coding: utf-8 -*-
"""Repeatable offscreen GUI baseline for the paged PySide6 catalog.

Usage:
    QT_QPA_PLATFORM=offscreen python tools/gui_performance_benchmark.py --sizes 50000 200000

The benchmark creates synthetic database rows and never opens production data
or real media files. It measures application construction, first page delivery,
and one debounced text query on the same local machine.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from core.video_organizer_service import VideoOrganizerService
from gui.main_window import MainWindow
from gui.workers.catalog_worker import CatalogQueryWorker
from tools.video_catalog_benchmark import populate


def pump_until(app: QApplication, predicate, timeout: float = 5.0) -> float | None:
    started = time.perf_counter()
    deadline = started + timeout
    while time.perf_counter() < deadline:
        app.processEvents()
        if predicate():
            return round((time.perf_counter() - started) * 1000, 3)
        time.sleep(0.005)
    app.processEvents()
    return None


def run(app: QApplication, size: int) -> dict:
    if size < 1:
        raise ValueError("基准至少需要一条素材")
    with tempfile.TemporaryDirectory(prefix=f"gui-catalog-{size}-") as tmp, patch.dict(
        os.environ, {"LOCALAPPDATA": tmp, "VIDEO_ORGANIZER_LOG_DIR": tmp}
    ):
        db_path = str(Path(tmp) / "benchmark.db")
        populate(db_path, size)
        settings = {
            "ui_preferences": {
                "theme": "dark",
                "font_size": 14,
                "default_view": "list",
                "detail_panel_expanded": True,
            },
            "global_settings": {},
        }
        started = time.perf_counter()
        service = VideoOrganizerService(
            settings=settings,
            db_path=db_path,
            results_json=str(Path(tmp) / "results.json"),
            results_csv=str(Path(tmp) / "results.csv"),
        )
        window = MainWindow(service)
        try:
            window.show()
            app.processEvents()
            startup_ms = round((time.perf_counter() - started) * 1000, 3)
            window.nav_list.setCurrentRow(1)
            page = window.library_page
            first_page_ms = pump_until(app, lambda: page._query_generation > 0 and not page._page_loading)
            if first_page_ms is None:
                raise TimeoutError("等待素材库首屏超时")
            first_rows = page.model.rowCount()
            if page._page_total != size or first_rows != min(size, page._page_limit):
                raise RuntimeError(f"素材库首屏不完整：总数 {page._page_total}，首屏 {first_rows}")

            old_generation = page._query_generation
            page.search_input.setText("clip-0001")
            query_ms = pump_until(app, lambda: page._query_generation > old_generation and not page._page_loading)
            if query_ms is None:
                raise TimeoutError("等待防抖检索结果超时")
            if page.status_label.text().startswith("查询失败"):
                raise RuntimeError(page.status_label.text())
            # populate 使用 clip-{i:07d}.mp4，因此此前缀只命中序号 [1000, 2000)。
            # 核对总数及实际交给 GUI 模型的首屏，不为验证基准再扫描整库。
            expected = max(0, min(size, 2000) - 1000)
            displayed = page.model.videos
            if (page._page_total != expected
                    or len(displayed) != min(expected, page._page_limit)
                    or not all(row["filename"].startswith("clip-0001") for row in displayed)):
                raise RuntimeError(f"检索结果不符：期望 {expected} 条，实际总数 {page._page_total}，"
                                   f"模型首屏 {len(displayed)} 条")
            return {
                "size": size,
                "startup_to_window_ms": startup_ms,
                "library_first_page_ms": first_page_ms,
                "debounced_text_query_ms": query_ms,
                "first_page_rows": first_rows,
                "filtered_rows": page._page_total,
            }
        finally:
            # 超时也要等 SQLite 查询退出，避免关闭时弹出忙碌对话框或销毁运行中的 QThread。
            for worker in window.findChildren(CatalogQueryWorker):
                worker.wait()
            app.processEvents()
            window._allow_exit = True
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def percentile(samples, fraction: float) -> float:
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 3)


def summarize(samples: list[dict], repeat: int) -> dict:
    result = {"size": samples[0]["size"], "repeat": repeat}
    for key in (
        "startup_to_window_ms",
        "library_first_page_ms",
        "debounced_text_query_ms",
    ):
        values = [sample[key] for sample in samples if sample[key] is not None]
        result[key] = {
            "samples_ms": values,
            "median_ms": round(statistics.median(values), 3) if values else None,
            "p95_ms": percentile(values, 0.95) if values else None,
        }
    result["first_page_rows"] = samples[-1]["first_page_rows"]
    result["filtered_rows"] = samples[-1]["filtered_rows"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", nargs="+", type=int, default=[50000])
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    repeat = max(1, int(args.repeat))
    results = []
    for size in args.sizes:
        samples = [run(app, size) for _ in range(repeat)]
        results.append(summarize(samples, repeat))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
