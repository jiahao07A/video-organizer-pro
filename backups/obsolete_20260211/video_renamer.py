# -*- coding: utf-8 -*-
"""
全自动视频整理工作流 (Video Tagger & Renamer Pro) v5.0
实现“标签提取 -> 自动补全 -> 标准化重命名”的一键式全自动闭环。

核心增强:
1. 自动闭环：如果重命名时发现标签不全，脚本会自动调用 AI 补全标签，然后继续执行重命名。
2. 模块化集成：直接在内部复用 AITagger 的逻辑，无需手动运行两个脚本。
3. 超级鲁棒搜索：继承了 v4.0 的深度搜索和指纹匹配技术。
4. 安全保障：自动备份、模拟运行、一键撤销。
"""

import os
import json
import re
import argparse
import shutil
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Set
from openai import OpenAI
from dotenv import load_dotenv
from tqdm import tqdm

# ================= 配置区域 =================
DEFAULT_INPUT = "video_analysis_results.json"
DEFAULT_DATA_FILE = "video_analysis_results_tagged.json"
CONFIG_FILE = ".env"
BACKUP_DIR = "backups"
UNDO_FILE = "undo_rename.py"

# 默认 AI 配置
DEFAULT_CONFIG = {
    "api_key": "1016a1016",
    "base_url": "https://vqnypowwewrv.ap-northeast-1.clawcloudrun.com/v1",
    "model_name": "gemini-2.5-flash",
    "max_workers": 10
}

# 标签体系
TAG_DIMENSIONS = {
    "1. 情感与风格 (Mood & Style)": ["唯美", "治愈", "悲伤", "喜悦", "科技感", "艺术感", "宁静", "紧张", "庄重"],
    "2. 人物主体 (Subject)": ["男性", "女性", "儿童", "老人", "情侣", "家庭", "团队", "手部特写", "剪影", "无人"],
    "3. 场景地点 (Location)": ["城市", "自然", "居家", "办公", "学校", "健身房", "公共场所", "抽象背景", "监狱"],
    "4. 行为动作 (Action)": ["工作", "学习", "运动", "交流", "生活", "饮食", "数码互动", "思考", "展示"],
    "5. 关键物品/元素 (Key Objects)": ["大脑", "金钱", "时间", "数据", "交通工具", "乐器", "医疗", "食物"]
}

# ============================================

class ConfigManager:
    @staticmethod
    def load_config() -> Dict:
        load_dotenv(CONFIG_FILE)
        config = DEFAULT_CONFIG.copy()
        config["api_key"] = os.getenv("OPENAI_API_KEY", config["api_key"])
        config["base_url"] = os.getenv("OPENAI_BASE_URL", config["base_url"])
        return config

class AITagger:
    def __init__(self, config):
        self.client = OpenAI(api_key=config["api_key"], base_url=config["base_url"], max_retries=2, timeout=20.0)
        self.model = config["model_name"]
        self.tag_lib_lock = threading.Lock()
        self.key_objects_lib = set(TAG_DIMENSIONS["5. 关键物品/元素 (Key Objects)"])

    def generate_tags(self, summary: str) -> List[str]:
        if not summary: return ["无"] * 5
        with self.tag_lib_lock:
            current_objects = ", ".join(sorted(list(self.key_objects_lib)))
        prompt = f"""你是一个专业的视频标签标注员。请根据提供的“视频内容摘要”，从以下5个维度中，为该视频各选出一个最符合的标签。
摘要: "{summary}"
标注规则:
1. 情感与风格: 必须从 [{', '.join(TAG_DIMENSIONS["1. 情感与风格 (Mood & Style)"])}] 中选择。
2. 人物主体: 必须从 [{', '.join(TAG_DIMENSIONS["2. 人物主体 (Subject)"])}] 中选择。
3. 场景地点: 必须从 [{', '.join(TAG_DIMENSIONS["3. 场景地点 (Location)"])}] 中选择。
4. 行为动作: 必须从 [{', '.join(TAG_DIMENSIONS["4. 行为动作 (Action)"])}] 中选择。
5. 关键物品: 优先从 [{current_objects}] 中选择。如果摘要中出现了更具有代表性的物品，允许你创建简洁的2-4字新标签。
仅返回5个标签，用英文逗号分隔。"""
        try:
            response = self.client.chat.completions.create(model=self.model, messages=[{"role": "user", "content": prompt}], temperature=0.1, max_tokens=100)
            res_content = response.choices[0].message.content
            if not res_content: return ["无"] * 5
            tags = [t.strip() for t in res_content.strip().replace("，", ",").split(",")][:5]
            while len(tags) < 5: tags.append("无")
            if tags[4] not in ["无", "未知", "超时"]:
                with self.tag_lib_lock: self.key_objects_lib.add(tags[4])
            return tags
        except: return ["超时"] * 5

class BackupManager:
    @staticmethod
    def create_backup(data_file):
        if not os.path.exists(BACKUP_DIR): os.makedirs(BACKUP_DIR)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = os.path.join(BACKUP_DIR, ts)
        os.makedirs(target)
        if os.path.exists(data_file): shutil.copy2(data_file, os.path.join(target, os.path.basename(data_file)))
        shutil.copy2(__file__, os.path.join(target, os.path.basename(__file__)))
        return target

    @staticmethod
    def restore(data_file):
        if not os.path.exists(BACKUP_DIR): return
        backups = sorted([d for d in os.listdir(BACKUP_DIR) if os.path.isdir(os.path.join(BACKUP_DIR, d))], reverse=True)
        if backups:
            src = os.path.join(BACKUP_DIR, backups[0], os.path.basename(data_file))
            if os.path.exists(src): shutil.copy2(src, data_file); print(f"已从 {backups[0]} 恢复数据。")

def sanitize_filename(name: str, max_len: int = 80) -> str:
    invalid_chars = r'[\\/:*?"<>|]'
    sanitized = re.sub(invalid_chars, "_", name)
    sanitized = sanitized.replace("\n", "").replace("\r", "").strip()
    if len(sanitized) > max_len: sanitized = sanitized[:max_len-3] + "..."
    return sanitized

def get_file_index(root_dir):
    index = {}
    for root, _, files in os.walk(root_dir):
        for f in files:
            index[f] = os.path.join(root, f)
            if "-" in f: index[f"suffix_{f.split('-')[-1]}"] = os.path.join(root, f)
    return index

def find_physical_file(item, index):
    p = item.get("path"); fn = item.get("filename")
    if p and os.path.exists(p): return p
    if fn in index: return index[fn]
    if fn and "-" in fn:
        sid = f"suffix_{fn.split('-')[-1]}"
        if sid in index: return index[sid]
    return None

def generate_undo_script(rename_log, data_file):
    content = ["# -*- coding: utf-8 -*-\nimport os, json\ndef undo():\n    mapping = {}\n"]
    for old, new in rename_log: content.append(f"    try:\n        if os.path.exists({repr(new)}):\n            os.rename({repr(new)}, {repr(old)})\n            mapping[{repr(new)}] = {repr(old)}\n    except: pass\n")
    content.append(f"    if os.path.exists({repr(data_file)}):\n        with open({repr(data_file)}, 'r', encoding='utf-8') as f: data = json.load(f)\n        for item in data:\n            p = item.get('path')\n            if p in mapping: item['path'] = mapping[p]; item['filename'] = os.path.basename(mapping[p])\n        with open({repr(data_file)}, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=2)\n")
    content.append("if __name__ == '__main__': undo(); print('还原成功')\n")
    with open(UNDO_FILE, "w", encoding="utf-8") as f: f.writelines(content)

def process_and_rename():
    parser = argparse.ArgumentParser(description="全自动视频整理闭环")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_DATA_FILE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()

    if args.restore: BackupManager.restore(args.output); return

    # 加载数据
    data_path = args.output if os.path.exists(args.output) else args.input
    if not os.path.exists(data_path): print("错误: 找不到数据源。"); return
    with open(data_path, "r", encoding="utf-8") as f: data = json.load(f)

    print("正在扫描目录建立索引...")
    file_index = get_file_index(os.getcwd())
    config = ConfigManager.load_config()
    tagger = AITagger(config)

    # 预加载已有标签库
    for item in data:
        if "tags" in item and len(item["tags"]) >= 5:
            obj = item["tags"][4]
            if obj and obj not in ["无", "未知", "超时"]: tagger.key_objects_lib.add(obj)

    if not args.dry_run: BackupManager.create_backup(data_path)

    # 识别需要打标的任务
    todo_tagging = [item for item in data if "tags" not in item or not item["tags"] or "超时" in item["tags"]]
    if todo_tagging:
        print(f"发现 {len(todo_tagging)} 个条目标签不全，开始自动补全...")
        pbar = tqdm(total=len(todo_tagging), desc="AI Tagging")
        def tag_task(it):
            it["tags"] = tagger.generate_tags(it.get("summary", ""))
            pbar.update(1)
        with ThreadPoolExecutor(max_workers=config["max_workers"]) as exe:
            list(exe.map(tag_task, todo_tagging))
        pbar.close()

    print("开始标准化重命名...")
    success_count = 0; skip_count = 0; rename_log = []
    
    for item in tqdm(data, desc="Renaming"):
        found_path = find_physical_file(item, file_index)
        if not found_path or not item.get("tags") or "超时" in item["tags"]:
            skip_count += 1; continue

        current_fn = os.path.basename(found_path)
        raw_fn = current_fn.split("-")[-1] if "-" in current_fn else current_fn
        tags_str = "_".join(item["tags"])
        safe_sum = sanitize_filename(item.get("summary", ""), max_len=60)
        new_fn = sanitize_filename(f"{item.get('category')}-{tags_str}-{safe_sum}-{raw_fn}", max_len=200)
        
        target_path = os.path.join(os.path.dirname(found_path), new_fn)
        if os.path.normpath(found_path) == os.path.normpath(target_path):
            skip_count += 1; continue

        if args.dry_run: print(f"[预览] {current_fn} -> {new_fn}"); success_count += 1; continue

        try:
            if os.path.exists(target_path):
                b, e = os.path.splitext(new_fn)
                target_path = os.path.join(os.path.dirname(found_path), f"{b}_dup{e}")
            os.rename(found_path, target_path)
            rename_log.append((found_path, target_path))
            item["path"] = target_path; item["filename"] = os.path.basename(target_path)
            success_count += 1
        except: pass

    if not args.dry_run:
        with open(args.output, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
        generate_undo_script(rename_log, args.output)
        print(f"\n工作流执行完成！数据已同步。")
    
    print(f"汇总: 成功 {success_count} | 跳过/未找到 {skip_count}")

if __name__ == "__main__":
    process_and_rename()
