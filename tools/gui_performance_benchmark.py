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
import gc
import json
import math
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from core.video_organizer_service import VideoOrganizerService
from gui.main_window import MainWindow
from video_catalog_benchmark import populate


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
    with tempfile.TemporaryDirectory(prefix=f"gui-catalog-{size}-") as tmp:
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
        window.show()
        app.processEvents()
        startup_ms = round((time.perf_counter() - started) * 1000, 3)

        window.nav_list.setCurrentRow(1)
        first_page_ms = pump_until(
            app,
            lambda: window.library_page is not None
            and window.library_page._page_offset > 0
            and not window.library_page._page_loading,
        )
        old_generation = window.library_page._query_generation
        query_started = time.perf_counter()
        window.library_page.search_input.setText("clip-0001")
        query_ms = pump_until(
            app,
            lambda: window.library_page._query_generation > old_generation
            and not window.library_page._page_loading
            and window.library_page._page_total < size,
        )
        if query_ms is None:
            query_ms = round((time.perf_counter() - query_started) * 1000, 3)

        result = {
            "size": size,
            "startup_to_window_ms": startup_ms,
            "library_first_page_ms": first_page_ms,
            "debounced_text_query_ms": query_ms,
            "first_page_rows": window.library_page.model.rowCount(),
            "filtered_rows": window.library_page._page_total,
        }
        window.close()
        app.processEvents()
        del window
        del service
        gc.collect()
        return result


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
