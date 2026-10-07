# -*- coding: utf-8 -*-
"""Repeatable local benchmark for the database-backed video catalog.

Usage:
    python tools/video_catalog_benchmark.py --sizes 50000 200000

The benchmark uses synthetic rows in a temporary SQLite database and never
opens the application's production database or real media files.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.video_catalog import VideoCatalog, VideoQuery
from core.video_organizer_service import DatabaseManager


def populate(db_path: str, size: int) -> None:
    db = DatabaseManager(db_path)
    conn = sqlite3.connect(db_path)
    now = "2026-10-05 12:00:00"
    rows = []
    for i in range(size):
        status = ("pending", "analyzed", "failed")[i % 3]
        category = "A-Roll" if i % 2 else "B-Roll"
        tags = json.dumps(["森林" if i % 5 == 0 else "室内", "主体"], ensure_ascii=False)
        rows.append(
            (
                f"/synthetic/library/{i // 1000:04d}/clip-{i:07d}.mp4",
                f"clip-{i:07d}.mp4",
                category,
                "synthetic summary" if i % 4 else "",
                tags,
                status,
                now,
            )
        )
    with conn:
        conn.executemany(
            """INSERT INTO videos
               (path, filename, category, summary, tags, status, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    conn.close()
    # Keep the local variable alive until initialization has completed.
    del db


def measure(fn):
    start = time.perf_counter()
    value = fn()
    return round((time.perf_counter() - start) * 1000, 3), value


def percentile(samples, fraction: float) -> float:
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 3)


def run(size: int, repeat: int = 3) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"video-catalog-{size}-") as tmp:
        db_path = str(Path(tmp) / "benchmark.db")
        populate(db_path, size)
        db_manager = DatabaseManager(db_path)
        catalog = VideoCatalog(db_manager)
        queries = {
            "first_page": lambda: catalog.list_page(VideoQuery(), limit=100),
            "status_page": lambda: catalog.list_page(
                VideoQuery(analysis_statuses=("analyzed",)), limit=100
            ),
            "text_page": lambda: catalog.list_page(VideoQuery(text="clip-0001"), limit=100),
            "tag_page": lambda: catalog.list_page(VideoQuery(tags=("森林",)), limit=100),
            "filename_page": lambda: catalog.list_page(
                VideoQuery(order_by="filename", descending=False), limit=100
            ),
            "count_all": lambda: catalog.count(VideoQuery()),
            "count_filtered": lambda: catalog.count(
                VideoQuery(analysis_statuses=("analyzed",), summary_empty="nonempty")
            ),
        }
        timings = {}
        result_sizes = {}
        try:
            for name, fn in queries.items():
                samples = []
                value = None
                for _ in range(max(1, int(repeat))):
                    elapsed, value = measure(fn)
                    samples.append(elapsed)
                timings[name] = {
                    "samples_ms": samples,
                    "median_ms": round(statistics.median(samples), 3),
                    "p95_ms": percentile(samples, 0.95),
                }
                result_sizes[name] = len(value.rows) if hasattr(value, "rows") else value
            return {"size": size, "repeat": max(1, int(repeat)), "timings_ms": timings, "result_sizes": result_sizes}
        finally:
            # DatabaseManager uses short-lived SQLite connections. Release all
            # owning objects before TemporaryDirectory removes the database on
            # Windows, where an open handle prevents cleanup.
            del catalog
            del db_manager
            gc.collect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", nargs="+", type=int, default=[50000])
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps([run(size, args.repeat) for size in args.sizes], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
