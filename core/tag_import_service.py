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
import re
from typing import List, Dict, Optional, Any, Tuple
from core.video_organizer_service import AIHandler, TAG_CONFIG_FILE
from core.storage_backup import STORAGE_IO_LOCK, write_json_atomic
from core.tag_vocab import parse_vocab_draft_line, parse_vocab_group_header

logger = logging.getLogger("VideoOrganizer.TagImport")

_NUMBERED_GROUP_HEADER_RE = re.compile(
    r"^\s*[一二三四五六七八九十]+、\s*(?P<title>[^：:]+)"
)
_NUMBERED_GROUP_IDS = (
    ("主体", "subject"),
    ("动作", "action"),
    ("场景", "location"),
    ("地点", "location"),
    ("情绪", "mood"),
    ("氛围", "mood"),
    ("时间", "custom"),
    ("天气", "custom"),
    ("画面风格", "custom"),
    ("镜头", "custom"),
    ("主题", "custom"),
)


def _numbered_group_id(line: str) -> Optional[str]:
    match = _NUMBERED_GROUP_HEADER_RE.match(str(line or "").strip())
    if not match:
        return None
    title = match.group("title")
    for marker, group_id in _NUMBERED_GROUP_IDS:
        if marker in title:
            return group_id
    return None


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

    def __init__(self, ai_handler: AIHandler, tag_config_path: Optional[str] = None):
        self.ai = ai_handler
        self.tag_config_path = tag_config_path or TAG_CONFIG_FILE
        self.last_import_aliases: Dict[str, str] = {}
        self.last_import_groups: Dict[str, str] = {}

    def _parse_tag_file_lines(
        self, raw_lines: List[str]
    ) -> Tuple[List[str], Dict[str, str], Dict[str, str], Dict[str, int]]:
        """解析标签词表，返回标准词、别名映射、来源组和解析统计。"""
        has_structured_headers = any(
            parse_vocab_group_header(line) or _numbered_group_id(line)
            for line in raw_lines
        )
        current_group = "custom"
        tags: List[str] = []
        aliases: Dict[str, str] = {}
        groups: Dict[str, str] = {}
        claimed: set[str] = set()
        seen_standards: set[str] = set()
        skipped_comments = 0
        skipped_headers = 0
        skipped_preamble = 0

        for raw_line in raw_lines:
            line = str(raw_line or "").strip()
            if not line:
                continue

            group_id = parse_vocab_group_header(line) or _numbered_group_id(line)
            if group_id:
                current_group = group_id
                skipped_headers += 1
                continue

            if line.startswith("#"):
                skipped_comments += 1
                continue

            # 带章节结构的词表，首个章节标题以前的说明文字不是标签。
            if has_structured_headers and not tags and current_group == "custom":
                skipped_preamble += 1
                continue

            parsed = parse_vocab_draft_line(line)
            if parsed is None:
                continue
            standard, line_aliases = parsed
            standard = standard.strip()
            if not standard:
                continue

            # 与现有词表冷启动语义一致：别名不再作为第二个标准词。
            if standard in claimed and standard not in seen_standards:
                continue
            if standard not in seen_standards:
                tags.append(standard)
                seen_standards.add(standard)
                groups[standard] = current_group
                claimed.add(standard)

            for alias in line_aliases:
                alias = alias.strip()
                if not alias or alias == standard or alias in claimed:
                    continue
                aliases[alias] = standard
                claimed.add(alias)

        stats = {
            "raw_line_count": len(raw_lines),
            "standard_count": len(tags),
            "alias_count": len(aliases),
            "comment_line_count": skipped_comments,
            "header_line_count": skipped_headers,
            "preamble_line_count": skipped_preamble,
            "structured_headers": int(has_structured_headers),
        }
        return tags, aliases, groups, stats

    def read_tags_from_file(self, file_path: str) -> List[str]:
        """从词表读取标准词；忽略注释/章节说明并拆出别名。"""
        if not os.path.exists(file_path):
            logger.error(f"标签文件不存在: {file_path}")
            return []

        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                raw_lines = f.readlines()
            tags, aliases, groups, _stats = self._parse_tag_file_lines(raw_lines)
            self.last_import_aliases = aliases
            self.last_import_groups = groups
            return tags
        except Exception as e:
            self.last_import_aliases = {}
            self.last_import_groups = {}
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

        input_tags = {str(tag).strip() for tag in tags if str(tag).strip()}
        for tag, cat_id in result.items():
            tag = str(tag or "").strip()
            if tag not in input_tags:
                continue
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
            with STORAGE_IO_LOCK:
                write_json_atomic(self.tag_config_path, config)
            logger.info("已成功保存分类后的标签到配置")
            return True, ""
        except Exception as e:
            msg = f"保存标签配置失败: {e}"
            logger.error(msg)
            return False, msg