# -*- coding: utf-8 -*-
"""Regression and performance tests for the database-backed video catalog."""
from pathlib import Path

from core.video_catalog import VideoCatalog, VideoQuery
from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService


def _service(tmp_path: Path) -> VideoOrganizerService:
    return VideoOrganizerService(
        settings={**DEFAULT_SETTINGS, "ui_preferences": dict(DEFAULT_SETTINGS.get("ui_preferences", {}))},
        on_log=lambda _m: None,
        db_path=str(tmp_path / "catalog.db"),
        results_json=str(tmp_path / "results.json"),
        results_csv=str(tmp_path / "results.csv"),
    )


def _insert(service, path: Path, *, status="pending", category="A-Roll", tags=None, summary=""):
    service.db.upsert_video(
        {
            "path": str(path.resolve()),
            "filename": path.name,
            "status": status,
            "category": category,
            "tags": tags or [],
            "summary": summary,
            "tag_groups": {},
        }
    )
    return str(path.resolve())


def test_catalog_page_projects_list_columns_and_orders(tmp_path):
    service = _service(tmp_path)
    _insert(service, tmp_path / "b.mp4", category="B-Roll")
    _insert(service, tmp_path / "a.mp4", category="A-Roll")

    catalog = VideoCatalog(service.db)
    page = catalog.list_page(VideoQuery(order_by="filename", descending=False), limit=1)

    assert page.total_count == 2
    assert [row["filename"] for row in page.rows] == ["a.mp4"]
    assert page.rows[0]["library_id"] == page.rows[0]["id"]
    assert "raw_metadata" not in page.rows[0]
    assert page.has_more


def test_catalog_filters_text_status_tags_and_summary(tmp_path):
    service = _service(tmp_path)
    _insert(
        service,
        tmp_path / "forest.mp4",
        status="analyzed",
        tags=["森林", "纪实"],
        summary="绿色树林",
    )
    _insert(
        service,
        tmp_path / "street.mp4",
        status="pending",
        tags=["街道"],
        summary="",
    )
    catalog = VideoCatalog(service.db)

    query = VideoQuery(
        text="森林",
        analysis_statuses=("analyzed",),
        tags=("森林",),
        summary_empty="nonempty",
    )
    assert [row["filename"] for row in catalog.list_page(query).rows] == ["forest.mp4"]


def test_catalog_tag_alias_expansion_and_all_mode(tmp_path):
    service = _service(tmp_path)
    _insert(service, tmp_path / "one.mp4", tags=["男性", "室内"])
    _insert(service, tmp_path / "two.mp4", tags=["女性", "室内"])
    catalog = VideoCatalog(service.db)

    query = VideoQuery(
        tags=("男人", "室内"),
        tag_match_mode="all",
        alias_map={"男人": "男性"},
    )
    assert [row["filename"] for row in catalog.list_page(query).rows] == ["one.mp4"]


def test_catalog_scope_and_visible_paths_ignore_pagination(tmp_path):
    service = _service(tmp_path)
    folder = tmp_path / "scope"
    folder.mkdir()
    paths = [_insert(service, folder / f"{i}.mp4") for i in range(4)]
    _insert(service, tmp_path / "outside.mp4")
    catalog = VideoCatalog(service.db)

    query = VideoQuery(scope_paths=(str(folder),))
    assert catalog.count(query) == 4
    assert catalog.list_page(query, limit=2).has_more
    assert set(catalog.resolve_visible_paths(query)) == set(paths)


def test_catalog_summary_and_status_aliases(tmp_path):
    service = _service(tmp_path)
    _insert(service, tmp_path / "empty.mp4", status="", summary=" ")
    _insert(service, tmp_path / "done.mp4", status="analyzed", summary="ok")
    catalog = VideoCatalog(service.db)

    empty = VideoQuery(summary_empty="empty", analysis_statuses=("pending",))
    assert [row["filename"] for row in catalog.list_page(empty).rows] == ["empty.mp4"]
    done = VideoQuery(analysis_statuses=("analyzed",))
    assert [row["filename"] for row in catalog.list_page(done).rows] == ["done.mp4"]
