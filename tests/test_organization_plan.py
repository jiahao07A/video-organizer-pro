from core.organization_plan import OrganizationPlan, PHYSICAL_MIGRATION


def _plan():
    return OrganizationPlan.from_migration_plan(
        [
            {
                "source_path": "C:/clips/a.mp4",
                "target_path": "D:/organized/A/a.mp4",
                "status": "ready",
            },
            {
                "source_path": "C:/clips/b.mp4",
                "target_path": "D:/organized/B/b.mp4",
                "status": "review",
                "risk_flags": ["目标已存在"],
            },
            {
                "source_path": "C:/clips/c.mp4",
                "target_path": "D:/organized/C/c.mp4",
                "action_type": "xmp",
            },
        ],
        work_scope_paths=["C:/clips"],
        target_paths=["C:/clips/a.mp4", "C:/clips/b.mp4", "C:/clips/c.mp4"],
        filter_summary="分类=B-Roll",
    )


def test_plan_excludes_one_material_without_mutating_original():
    plan = _plan()
    updated = plan.exclude_paths(["C:/clips/b.mp4"])

    assert plan.summary()["active_items"] == 3
    assert updated.summary()["active_items"] == 2
    assert updated.summary()["excluded_items"] == 1
    assert updated.active_source_paths == ("C:/clips/a.mp4", "C:/clips/c.mp4")
    assert updated.items[1].exclusion_reason == "用户排除素材"


def test_plan_path_exclusion_is_normalized_and_preserves_first_reason():
    plan = _plan().exclude_paths(["c:\\clips\\b.mp4"], reason="第一次排除")
    updated = plan.exclude_paths(["C:/clips/b.mp4"], reason="第二次排除")

    assert updated.items[1].excluded is True
    assert updated.items[1].exclusion_reason == "第一次排除"


def test_plan_excludes_operation_type_and_reports_counts():
    plan = _plan().exclude_action_types(["xmp"])

    assert plan.summary()["active_items"] == 2
    assert plan.summary()["active_by_action"] == {PHYSICAL_MIGRATION: 2}
    assert plan.items[2].excluded is True
    assert plan.items[2].exclusion_reason == "用户排除操作类型"


def test_plan_summary_preserves_risk_without_claiming_execution():
    plan = _plan()

    assert plan.summary()["plan_id"]
    assert plan.summary()["work_scope_paths"] == ("C:/clips",)
    assert plan.summary()["work_scope_count"] == 1
    assert plan.summary()["target_count"] == 3
    assert plan.summary()["filter_summary"] == "分类=B-Roll"
    assert plan.items[1].status == "review"
    assert plan.items[1].risk_flags == ("目标已存在",)
