# -*- coding: utf-8 -*-
"""
视频标签自动生成脚本 (AI Video Tagger) v3.0 (Dynamic Discovery)
基于已有的 video_analysis_results.json 中的 summary 内容，通过 AI 进一步提取 5 个维度的标签。

功能增强:
1. 动态标签发现：允许 AI 在“关键物品”维度自由发挥，并自动将新发现的标签加入库中。
2. 严格限制：除“关键物品”外，其他维度严格遵守预设列表。
3. 多线程并发：支持批量快速处理，大幅提升效率。
4. 命令行支持：可自定义输入输出路径。
5. CSV 导出：同步生成 CSV 文件，方便表格管理。
6. 配置向导：首次运行自动引导设置 API。
7. 断点续传：自动检测已处理项目。
"""

import os
import json
import time
import argparse
import threading
import csv
from typing import List, Dict, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from dotenv import load_dotenv

# 尝试导入 tqdm
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

# ================= 配置区域 =================
CONFIG_FILE = ".env"
DEFAULT_INPUT = "video_analysis_results.json"
DEFAULT_OUTPUT_JSON = "video_analysis_results_tagged.json"
DEFAULT_OUTPUT_CSV = "video_analysis_results_tagged.csv"

DEFAULT_CONFIG = {
    "api_key": "1016a1016",
    "base_url": "https://vqnypowwewrv.ap-northeast-1.clawcloudrun.com/v1",
    "model_name": "gemini-2.5-flash",
    "max_workers": 10  # 默认并发数
}

# 标签体系
TAG_DIMENSIONS = {
    "1. 情感与风格 (Mood & Style)": ["唯美", "治愈", "悲伤", "喜悦", "科技感", "艺术感", "宁静", "紧张", "庄重"],
    "2. 人物主体 (Subject)": ["男性", "女性", "儿童", "老人", "情侣", "家庭", "团队", "手部特写", "剪影", "无人"],
    "3. 场景地点 (Location)": ["城市", "自然", "居家", "办公", "学校", "健身房", "公共场所", "抽象背景","监狱"],
    "4. 行为动作 (Action)": ["工作", "学习", "运动", "交流", "生活", "饮食", "数码互动", "思考", "展示"],
    "5. 关键物品/元素 (Key Objects)": ["大脑", "金钱", "时间", "数据", "交通工具", "乐器", "医疗", "食物"]
}

# ============================================

class ConfigManager:
    @staticmethod
    def load_config() -> Dict:
        load_dotenv(CONFIG_FILE)
        config = DEFAULT_CONFIG.copy()
        
        env_api_key = os.getenv("OPENAI_API_KEY")
        if env_api_key: config["api_key"] = env_api_key
            
        env_base_url = os.getenv("OPENAI_BASE_URL")
        if env_base_url: config["base_url"] = env_base_url
            
        if not config["api_key"]:
            print("\n未检测到 OPENAI_API_KEY，进入配置向导...")
            key_input = input("请输入您的 API Key: ").strip()
            if key_input:
                config["api_key"] = key_input
                save = input("是否保存到 .env? (y/n): ").lower()
                if save == 'y':
                    with open(CONFIG_FILE, "a", encoding="utf-8") as f:
                        f.write(f"\nOPENAI_API_KEY={key_input}")
        return config

class AITagger:
    def __init__(self, config):
        self.client = OpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
            max_retries=2,
            timeout=20.0
        )
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
5. 关键物品: 优先从 [{current_objects}] 中选择。如果摘要中出现了更具有代表性的关键物品（如：钢琴、书、相机、手机等），允许你跳出列表创建一个简洁的2-4字新标签。

返回格式: 仅返回5个标签，用英文逗号分隔，不要有任何多余文字。"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=100
            )
            res_content = response.choices[0].message.content
            if not res_content: return ["无"] * 5
            res = res_content.strip()
            tags = [t.strip() for t in res.replace("，", ",").split(",")][:5]
            while len(tags) < 5: tags.append("无")
            
            # 动态更新关键物品库
            new_obj = tags[4]
            if new_obj and new_obj not in ["无", "未知", "超时"]:
                with self.tag_lib_lock:
                    self.key_objects_lib.add(new_obj)
            
            return tags
        except Exception:
            return ["超时"] * 5

class ResultsManager:
    def __init__(self, json_path: str, csv_path: str):
        self.json_path = json_path
        self.csv_path = csv_path
        self.lock = threading.Lock()
        self.data = []

    def load(self, input_path):
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
                    return True
            except: pass
        
        if os.path.exists(input_path):
            with open(input_path, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
                return True
        return False

    def save(self):
        with self.lock:
            # Save JSON
            with open(self.json_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            
            # Save CSV
            try:
                with open(self.csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f)
                    writer.writerow(['文件名', '分类', '摘要', '标签1(风格)', '标签2(主体)', '标签3(场景)', '标签4(动作)', '标签5(物品)'])
                    for item in self.data:
                        tags = item.get("tags", ["", "", "", "", ""])
                        writer.writerow([
                            item.get('filename', ''),
                            item.get('category', ''),
                            item.get('summary', ''),
                            *tags
                        ])
            except Exception as e:
                print(f"CSV 保存失败: {e}")

def process_item(item, tagger, results_manager, pbar):
    if "tags" not in item or not item["tags"] or "超时" in item["tags"]:
        tags = tagger.generate_tags(item.get("summary", ""))
        item["tags"] = tags
        # 每10条数据保存一次
        if pbar and pbar.n % 10 == 0:
            results_manager.save()
    
    if pbar: pbar.update(1)

def main():
    parser = argparse.ArgumentParser(description="AI 视频标签进一步处理工具")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="原始 JSON 文件路径")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_JSON, help="输出 JSON 文件路径")
    parser.add_argument("--csv", default=DEFAULT_OUTPUT_CSV, help="输出 CSV 文件路径")
    parser.add_argument("--workers", type=int, default=DEFAULT_CONFIG["max_workers"], help="并发线程数")
    args = parser.parse_args()

    config = ConfigManager.load_config()
    tagger = AITagger(config)
    rm = ResultsManager(args.output, args.csv)

    if not rm.load(args.input):
        print(f"无法加载数据，请确保 {args.input} 存在。")
        return

    # 初始化已有的关键物品库（从现有 tags 中学习）
    for item in rm.data:
        if "tags" in item and len(item["tags"]) >= 5:
            obj = item["tags"][4]
            if obj and obj not in ["无", "未知", "超时"]:
                tagger.key_objects_lib.add(obj)

    # 找出待处理项
    todo = [item for item in rm.data if "tags" not in item or not item["tags"] or "超时" in item["tags"]]
    print(f"总计: {len(rm.data)} | 待处理: {len(todo)} | 线程数: {args.workers}")

    if not todo:
        print("所有数据已标注完成。正在生成最终文件...")
        rm.save()
        return

    pbar = tqdm(total=len(todo), desc="AI Tagging") if tqdm else None
    
    # 注意：由于 AI 需要根据当前 lib 选择标签，如果并发太高可能导致新发现的标签没有及时反馈给其他线程
    # 但由于 lib 是实时更新的，这种“滞后”是可以接受的
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(process_item, item, tagger, rm, pbar) for item in todo]
        for _ in as_completed(futures): pass

    if pbar: pbar.close()
    rm.save()
    
    print(f"\n全部处理完成！")
    print(f"新增关键物品标签: {sorted(list(tagger.key_objects_lib - set(TAG_DIMENSIONS['5. 关键物品/元素 (Key Objects)'])))}")
    print(f"JSON: {args.output}\nCSV: {args.csv}")

if __name__ == "__main__":
    main()
