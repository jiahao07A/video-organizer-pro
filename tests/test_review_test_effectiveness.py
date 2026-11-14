"""以真实数据库和XML解析验证XMP输出的内容及选中边界。"""
import xml.etree.ElementTree as ET

import pytest

from core.video_organizer_service import SettingsManager, VideoOrganizerService


NAMESPACES = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "lr": "http://ns.adobe.com/lightroom/1.0/",
}


@pytest.mark.parametrize("hierarchical,prefix_category", [(True, True), (True, False), (False, True)])
def test_xmp_preserves_all_selected_metadata_and_excludes_other_materials(
    tmp_path, hierarchical, prefix_category,
):
    service = VideoOrganizerService(
        settings=SettingsManager.deep_copy_defaults(),
        db_path=str(tmp_path / "catalog.db"),
        on_log=lambda _message: None,
    )
    service.tag_config = {
        "global_settings": {
            "language": "zh-CN",
            "export_schemes": {
                "xmp_hierarchical": hierarchical,
                "xmp_prefix_category": prefix_category,
            },
        },
        "tag_groups": [{"id": "custom", "name": "建议", "tags": ["细节<近景>"]}],
    }
    service.db.add_tag("custom", "地点")
    parent = next(tag for tag in service.db.get_tags_detail() if tag["tag_name"] == "地点")
    service.db.add_tag("custom", "室内&大厅", parent_id=parent["id"])
    expected = {}
    for index in range(3):
        video = tmp_path / f"合成素材{index}.mp4"
        video.write_bytes(f"synthetic-{index}".encode())
        metadata = {
            "path": str(video),
            "filename": video.name,
            "status": "analyzed",
            "category": f"分类{index}&纪实",
            "summary": f"摘要{index}：画面 <窗口> 与人物 & 标志",
            "tags": ["室内&大厅", "细节<近景>", f"未分组词{index}"],
        }
        service.db.upsert_video(metadata)
        if index < 2:
            expected[video.with_suffix(".xmp")] = metadata

    selected = [str(sidecar.with_suffix(".mp4")) for sidecar in expected]
    result = service.sync_metadata_to_xmp(selected, return_details=True)
    assert result == {"planned_count": 2, "success_count": 2, "failed_count": 0, "failed_items": []}
    assert set(tmp_path.glob("*.xmp")) == set(expected)
    for sidecar, metadata in expected.items():
        root = ET.parse(sidecar)
        subject = root.findall(".//dc:subject/rdf:Bag/rdf:li", NAMESPACES)
        assert [node.text for node in subject] == metadata["tags"]
        summary = root.find(".//dc:description/rdf:Alt/rdf:li", NAMESPACES)
        assert summary is not None
        assert summary.attrib == {"{http://www.w3.org/XML/1998/namespace}lang": "x-default"}
        assert summary.text == metadata["summary"]
        hierarchy = [node.text for node in root.findall(".//lr:hierarchicalSubject/rdf:Bag/rdf:li", NAMESPACES)]
        expected_hierarchy = (
            ["地点|室内&大厅", "建议|细节<近景>", metadata["tags"][2]]
            if hierarchical else metadata["tags"]
        )
        if prefix_category:
            expected_hierarchy = [f"分类|{metadata['category']}", *expected_hierarchy]
        assert hierarchy == expected_hierarchy
