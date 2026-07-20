# -*- coding: utf-8 -*-
"""
Tag Import Service v1.0
Handles importing tags from external files and classifying them using AI.
"""

import os
import json
import logging
from typing import List, Dict, Optional
from core.video_organizer_service import AIHandler, TAG_CONFIG_FILE

logger = logging.getLogger("VideoOrganizer.TagImport")

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
            return list(set(tags)) # 去重
        except Exception as e:
            logger.error(f"读取标签文件失败: {e}")
            return []

    def classify_tags_with_ai(self, tags: List[str]) -> Dict[str, List[str]]:
        """调用 Gemini API 将标签归类到定义的分类中"""
        if not tags:
            return {}

        # 获取当前配置中的分类定义
        config = self.ai.tag_config
        if not config or "config" not in config:
            logger.error("标签配置无效，无法进行分类")
            return {}

        categories = []
        for item in config["config"]:
            categories.append({
                "id": item["id"],
                "display_name": item["display_name"],
                "description": item.get("description", "")
            })

        system_prompt = "你是一个视频标签分类专家。请将用户提供的标签列表映射到最合适的分类 ID 中。如果语义模糊或不属于前 4 个分类，请归类到 'custom'。"
        
        user_prompt = f"""
待分类标签列表:
{", ".join(tags)}

可用分类定义:
{json.dumps(categories, ensure_ascii=False, indent=2)}

要求:
1. 返回 JSON 格式，键为标签名，值为对应的分类 ID。
2. 即使没有完美匹配，也请根据语义选择最接近的分类。
3. 格式示例: {{"标签A": "mood", "标签B": "subject"}}
"""

        # 调用 AI
        # 注意：这里使用文本模式调用，因为不是图像分析
        # 获取模型路由
        from core.video_organizer_service import SettingsManager
        model_map = SettingsManager.get_setting(self.ai.settings, "api.model_personalization", {})
        model = model_map.get("tag_generation", "gemini-2.0-flash")

        result = self.ai._get_api_response(
            model=model,
            system_prompt=system_prompt,
            content_parts=[{"type": "text", "text": user_prompt}],
            json_mode=True
        )

        if not result:
            logger.warning("AI 标签分类返回为空或失败")
            return {}

        # 将结果转换为 {分类ID: [标签列表]} 格式
        classified = {cat["id"]: [] for cat in categories}
        for tag, cat_id in result.items():
            if cat_id in classified:
                if tag not in classified[cat_id]:
                    classified[cat_id].append(tag)
            else:
                # 兜底到 custom
                if "custom" in classified:
                    classified["custom"].append(tag)
        
        return classified

    def persist_classified_tags(self, classified_tags: Dict[str, List[str]]):
        """将分类后的标签合并并保存回 tag_config.json"""
        config = self.ai.tag_config
        if not config or "config" not in config:
            return

        for item in config["config"]:
            cat_id = item["id"]
            if cat_id in classified_tags:
                new_tags = classified_tags[cat_id]
                existing_tags = set(item.get("tags", []))
                for t in new_tags:
                    existing_tags.add(t)
                item["tags"] = sorted(list(existing_tags))

        # 写入文件
        try:
            with open(TAG_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            logger.info("已成功保存分类后的标签到配置")
        except Exception as e:
            logger.error(f"保存标签配置失败: {e}")
