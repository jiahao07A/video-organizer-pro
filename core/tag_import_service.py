# -*- coding: utf-8 -*-
"""
Tag Import Service v1.0
Handles importing tags from external files and classifying them using AI.
兼容 tag_config v6：`tag_groups`（不再要求旧键 `config`）。
标准词写入必须指定合法标签组；拒绝 pool / 缺组。
"""

import os
import json
import logging
from typing import List, Dict, Optional, Any, Tuple
from core.video_organizer_service import AIHandler, TAG_CONFIG_FILE

logger = logging.getLogger("VideoOrganizer.TagImport")


def _tag_groups_from_config(config: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """读取标签组定义：优先 tag_groups，兼容旧 config 列表。"""
    if not config:
        return []
    if config.get("tag_groups"):
        return list(config["tag_groups"])
    if config.get("config"):
        # 旧结构：config 即组列表
        return list(config["config"])
    return []


class TagImportService:
    """服务类，用于处理标签导入与 AI 分类"""

    def __init__(self, ai_handler: AIHandler):
        self.ai = ai_handler

    def read_tags_from_file(self, file_path: str) -> List[str]:
        """从 .txt 文件读取标签，每行一个"""
        if not os.path.exists(file_path):
            logger.error(f"标签文件不存在: {file_path}")
            return []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                tags = [line.strip() for line in f if line.strip()]
            return list(set(tags))  # 去重
        except Exception as e:
            logger.error(f"读取标签文件失败: {e}")
            return []

    def classify_tags_with_ai(self, tags: List[str]) -> Dict[str, List[str]]:
        """调用 AI 将标签归类到现有标签组"""
        if not tags:
            return {}

        config = self.ai.tag_config
        groups = _tag_groups_from_config(config)
        if not groups:
            logger.error("标签配置无效（无 tag_groups），无法进行分类")
            return {}

        categories = []
        for item in groups:
            rules = item.get("rules") or {}
            gid = item.get("id")
            if not gid or str(gid).lower() == "pool":
                continue
            categories.append(
                {
                    "id": gid,
                    "display_name": item.get("name") or item.get("display_name") or gid,
                    "description": rules.get("local_prompt") or item.get("description") or "",
                }
            )
        categories = [c for c in categories if c.get("id")]
        if not categories:
            logger.error("标签组缺少 id，无法进行分类")
            return {}

        system_prompt = (
            "你是一个视频标签分类专家。请将用户提供的标签列表映射到最合适的标签组 ID 中。"
            "如果语义模糊或不属于前几个封闭组，请归类到 'custom'（建议组）。"
            "禁止使用 pool 或中转池。"
        )

        user_prompt = f"""
待分类标签列表:
{", ".join(tags)}

可用标签组定义:
{json.dumps(categories, ensure_ascii=False, indent=2)}

要求:
1. 返回 JSON 格式，键为标签名，值为对应的组 ID。
2. 即使没有完美匹配，也请根据语义选择最接近的组。
3. 格式示例: {{"标签A": "mood", "标签B": "subject"}}
4. 禁止输出 pool。
"""

        result = self.ai._get_api_response(
            model="",
            system_prompt=system_prompt,
            content_parts=[{"type": "text", "text": user_prompt}],
            task_key="tag_generation",
        )

        if not result:
            logger.warning("AI 标签分类返回为空或失败")
            return {}

        # 将结果转换为 {组ID: [标签列表]} 格式
        classified = {cat["id"]: [] for cat in categories}
        if not isinstance(result, dict):
            logger.warning("AI 标签分类返回非 dict")
            return {}

        for tag, cat_id in result.items():
            cid = str(cat_id or "").strip()
            if cid.lower() == "pool":
                # 中转池已废除：落到 custom 或最后一组
                cid = "custom" if "custom" in classified else categories[-1]["id"]
            if cid in classified:
                if tag not in classified[cid]:
                    classified[cid].append(tag)
            else:
                if "custom" in classified:
                    classified["custom"].append(tag)
                else:
                    fallback = next(
                        (c["id"] for c in categories if c["id"] == "custom"),
                        categories[-1]["id"],
                    )
                    classified.setdefault(fallback, []).append(tag)

        return classified

    def persist_classified_tags(self, classified_tags: Dict[str, List[str]]) -> Tuple[bool, str]:
        """
        将分类后的标签合并并保存回 tag_config.json（v6 tag_groups）。
        拒绝 pool / 缺合法 group_id 的标准词写入。
        返回 (ok, error_message)。
        """
        from core.tag_group_ops import known_group_ids, reject_pool_in_classified

        config = self.ai.tag_config
        groups = _tag_groups_from_config(config)
        if not config or not groups:
            msg = "无法持久化：tag_config 无有效标签组"
            logger.error(msg)
            return False, msg

        known = known_group_ids(groups)
        ok, err = reject_pool_in_classified(classified_tags or {}, known)
        if not ok:
            logger.error(f"导入拒绝: {err}")
            return False, err

        # 确保写入 tag_groups 键
        if "tag_groups" not in config and groups is config.get("config"):
            config["tag_groups"] = groups

        target_groups = config.get("tag_groups") or groups
        for item in target_groups:
            cat_id = item.get("id")
            if cat_id not in classified_tags:
                continue
            if not cat_id or str(cat_id).lower() == "pool":
                continue
            new_tags = classified_tags[cat_id]
            existing = item.get("tags") or []
            existing_names = set()
            for t in existing:
                if isinstance(t, dict):
                    existing_names.add(t.get("name"))
                else:
                    existing_names.add(t)
            for t in new_tags:
                if t and t not in existing_names:
                    existing.append(t)
                    existing_names.add(t)
            item["tags"] = existing

        config["tag_groups"] = target_groups
        try:
            with open(TAG_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            logger.info("已成功保存分类后的标签到配置")
            return True, ""
        except Exception as e:
            msg = f"保存标签配置失败: {e}"
            logger.error(msg)
            return False, msg