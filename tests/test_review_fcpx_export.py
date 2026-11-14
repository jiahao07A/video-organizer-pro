"""FCPXML 元数据往返、资源引用与操作目标边界回归。"""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname
import xml.etree.ElementTree as ET

import pytest

from core.video_organizer_service import SettingsManager, VideoOrganizerService


@pytest.fixture
def service(tmp_path):
    return VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(), on_log=lambda _: None,
        db_path=str(tmp_path / "library.db"), results_json=str(tmp_path / "results.json"),
    )


def _video(service, path, *, name=None, category="纪实", tags=None, summary="合成摘要"):
    path.write_bytes(path.name.encode("utf-8"))
    row = {
        "path": str(path), "filename": name or path.name, "status": "analyzed",
        "category": category, "tags": tags or [], "summary": summary,
    }
    service.db.upsert_video(row)
    return row


def test_fcpx_xml_round_trips_special_metadata_and_file_uri(service, tmp_path):
    path = tmp_path / "中文 空格 & #100% '片'.mp4"
    row = _video(service, path, name='片段 "甲" <展示> & 名称', category='类别 "A" & <B>',
                 tags=['标签 & 一', '<标签二>', '中文 "三"', '第,四', '第五'],
                 summary='A & B < C > D，"对白"\n第二行')
    output = tmp_path / "special.fcpxml"
    assert service.export_to_fcpx_xml(str(output), [str(path)]) is None

    root = ET.parse(output).getroot()
    assert root.attrib == {"version": "1.7"}
    asset = root.find("resources/asset")
    event = root.find("library/event")
    clip = event.find("asset-clip")
    assert asset is not None and clip is not None
    assert re.fullmatch(r"[A-Za-z_][\w.-]*", asset.get("id"))
    assert clip.get("ref") == asset.get("id")
    assert asset.get("name") == clip.get("name") == row["filename"]
    assert asset.get("src") == path.resolve().as_uri()
    uri = urlsplit(asset.get("src"))
    assert uri.scheme == "file" and not uri.query and not uri.fragment
    assert Path(url2pathname(uri.path)).resolve() == path.resolve()
    assert "#" in unquote(uri.path) and "%" in unquote(uri.path)
    assert clip.findtext("note") == row["summary"]
    assert [keyword.get("value") for keyword in clip.findall("keyword")] == [row["category"], *row["tags"]]
    assert {collection.get("name") for collection in event.findall("keyword-collection")} == {row["category"], *row["tags"]}
    assert event.find("asset") is None
    assert "duration" not in asset.attrib and "duration" not in clip.attrib


def test_fcpx_scope_none_empty_normalized_and_collections_deduplicate(service, tmp_path):
    first = _video(service, tmp_path / "one.mp4", tags=["共享", "独有", "共享"])
    second = _video(service, tmp_path / "two.mp4", tags=["共享"])
    all_output = tmp_path / "all.fcpxml"
    service.export_to_fcpx_xml(str(all_output), None)
    root = ET.parse(all_output).getroot()
    assets = root.findall("resources/asset")
    assert {asset.get("name") for asset in assets} == {first["filename"], second["filename"]}
    assert len({asset.get("id") for asset in assets}) == 2
    assert {clip.get("ref") for clip in root.findall("library/event/asset-clip")} == {asset.get("id") for asset in assets}
    collections = root.findall("library/event/keyword-collection")
    assert len(collections) == 3
    assert {node.get("name") for node in collections} == {"纪实", "共享", "独有"}

    selected = tmp_path / "selected.fcpxml"
    alternate = str(tmp_path) + "/./one.mp4"
    assert alternate != first["path"]
    service.export_to_fcpx_xml(str(selected), [alternate])
    assert [node.get("name") for node in ET.parse(selected).findall("resources/asset")] == [first["filename"]]
    empty = tmp_path / "empty.fcpxml"
    assert service.export_to_fcpx_xml(str(empty), []) is False
    assert not empty.exists()


def test_fcpx_export_excludes_real_duplicate_copies(service, tmp_path):
    original = tmp_path / "original.mp4"
    duplicate = tmp_path / "copy.mp4"
    original.write_bytes(b"same synthetic video")
    duplicate.write_bytes(original.read_bytes())
    first = service.media_registry.register(str(original))
    second = service.media_registry.register(str(duplicate))
    assert second["duplicate_of"] == first["id"]
    output = tmp_path / "filtered.fcpxml"
    service.export_to_fcpx_xml(str(output), [str(original), str(duplicate)])
    assert [asset.get("src") for asset in ET.parse(output).findall("resources/asset")] == [original.resolve().as_uri()]
    rejected = tmp_path / "rejected.fcpxml"
    assert service.export_to_fcpx_xml(str(rejected), [str(duplicate)]) is False
    assert not rejected.exists()
