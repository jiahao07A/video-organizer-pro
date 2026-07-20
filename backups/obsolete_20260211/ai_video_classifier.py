# -*- coding: utf-8 -*-
"""
视频内容自动识别与分类脚本 (Video Content Analysis & Classification) v3.0 (Optimized)

功能描述:
1. 智能识别：支持单个视频文件或整个文件夹的批量处理。
2. 自动配置：首次运行自动引导设置OpenAI API Key。
3. 结果导出：分析结果自动保存为 JSON 和 CSV 文件。
4. 容错处理：自动跳过无法读取的文件，异常情况不中断任务。
5. 并发处理：多线程加速视频处理与分析。
6. 断点续传：支持跳过已处理的视频文件。

环境依赖:
pip install opencv-python openai numpy tqdm python-dotenv
"""

import os
import cv2
import base64
import json
import time
import numpy as np
import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Dict, Union, Set
from openai import OpenAI, OpenAIError
from dotenv import load_dotenv

# 尝试导入 tqdm 用于显示进度条
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

# ================= 配置区域 (Configuration) =================

# 默认配置
DEFAULT_CONFIG = {
    "api_key": "1016a1016",
    "base_url": "https://vqnypowwewrv.ap-northeast-1.clawcloudrun.com/v1",
    "model_name": "gemini-2.5-flash",
    "max_frames": 10,
    "target_size": 512,
    "jpeg_quality": 80,
    "max_workers": 4  # 并发线程数
}

CONFIG_FILE = ".env"
RESULTS_FILE_JSON = "video_analysis_results.json"
RESULTS_FILE_CSV = "video_analysis_results.csv"

# 预置分类标签
CATEGORIES = [
    "Aroll", "Broll", "工作", "学习", "生活", "美食", "科技", "人文", 
    "艺术", "运动", "动物", "自然", "城市", "平静", "悲伤", "喜悦", 
    "治愈", "震撼", "唯美", "人物", "风景", "静物"
]

# ===========================================================

class ConfigManager:
    """管理配置信息，支持从环境变量、.env文件加载"""
    
    @staticmethod
    def load_config() -> Dict:
        load_dotenv(CONFIG_FILE)
        config = DEFAULT_CONFIG.copy()
        
        env_api_key = os.getenv("OPENAI_API_KEY")
        if env_api_key:
            config["api_key"] = env_api_key
            
        env_base_url = os.getenv("OPENAI_BASE_URL")
        if env_base_url:
            config["base_url"] = env_base_url
            
        if not config["api_key"]:
            print("\n" + "="*50)
            print("首次运行配置向导")
            print("="*50)
            print("未检测到 OPENAI_API_KEY。")
            key_input = input("请输入您的 API Key (回车跳过): ").strip()
            save_choice = 'n'
            if key_input:
                config["api_key"] = key_input
                save_choice = input("是否保存到 .env 文件? (y/n): ").lower()
                if save_choice == 'y':
                    ConfigManager.save_to_env("OPENAI_API_KEY", key_input)
            
            url_input = input(f"请输入 API Base URL (默认为 {config['base_url']}): ").strip()
            if url_input:
                config["base_url"] = url_input
                if save_choice == 'y':
                     ConfigManager.save_to_env("OPENAI_BASE_URL", url_input)

        return config

    @staticmethod
    def save_to_env(key: str, value: str):
        lines = []
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        
        key_found = False
        new_lines = []
        for line in lines:
            if line.startswith(f"{key}="):
                new_lines.append(f"{key}={value}\n")
                key_found = True
            else:
                new_lines.append(line)
        
        if not key_found:
            new_lines.append(f"{key}={value}\n")
            
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

class VideoProcessor:
    """处理视频文件：读取、抽帧、压缩"""
    
    @staticmethod
    def resize_image(image: np.ndarray, target_size: int) -> np.ndarray:
        h, w = image.shape[:2]
        if max(h, w) <= target_size:
            return image
        scale = target_size / max(h, w)
        new_w, new_h = int(w * scale), int(h * scale)
        return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)

    @staticmethod
    def encode_image_to_base64(image: np.ndarray, quality: int = 80) -> str:
        try:
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
            success, buffer = cv2.imencode('.jpg', image, encode_param)
            if not success:
                return ""
            return base64.b64encode(buffer).decode('utf-8')
        except Exception:
            return ""

    @classmethod
    def extract_frames(cls, video_path: str, max_frames: int = 10, target_size: int = 512) -> List[str]:
        if not os.path.exists(video_path):
            return []

        base64_frames = []
        cap = cv2.VideoCapture(video_path)
        
        try:
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total_frames <= 0: total_frames = 1000 
            
            interval = max(1, total_frames // max_frames)
            indices = [i * interval for i in range(min(max_frames, total_frames))]
            if not indices and total_frames > 0: indices = [0]

            for frame_idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()
                if ret:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    resized_frame = cls.resize_image(frame_rgb, target_size)
                    base64_str = cls.encode_image_to_base64(resized_frame)
                    if base64_str:
                        base64_frames.append(base64_str)
        except Exception as e:
            # 仅在非线程安全打印时可能会乱，这里暂时忽略具体错误，依靠上层处理
            pass
        finally:
            cap.release()
            
        return base64_frames

class AIClassifier:
    """封装OpenAI API调用逻辑"""
    
    def __init__(self, api_key: str, base_url: str, model_name: str):
        if not api_key:
            raise ValueError("API Key is missing")
        # 减少重试次数，避免长时间卡死
        self.client = OpenAI(api_key=api_key, base_url=base_url, max_retries=1, timeout=30.0)
        self.model_name = model_name

    def analyze_video_content(self, base64_frames: List[str]) -> Optional[Dict]:
        if not base64_frames:
            return None

        categories_str = ", ".join(CATEGORIES)
        system_prompt = (
            "你是一个专业的视频内容分析师。请根据提供的视频关键帧序列，分析视频的主要内容。"
            "你需要完成两个任务：\n"
            f"1. 从以下标签中选择最匹配的一个分类：[{categories_str}]。\n"
            "2. 提供一段简短的视频内容摘要（50字以内）。\n\n"
            "请直接以JSON格式返回结果，格式如下：\n"
            "```json\n"
            "{\n"
            '  "category": "选定的分类",\n'
            '  "summary": "内容摘要"\n'
            "}\n"
            "```"
        )

        content_parts: List[Dict[str, Union[str, Dict[str, str]]]] = [{"type": "text", "text": "请分析这组视频关键帧。"}]
        for b64_img in base64_frames:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_img}", "detail": "low"}
            })

        try:
            # Pylance ignore
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_parts} 
            ]
            
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages, # type: ignore
                max_tokens=300,
                temperature=0.3,
            )
            
            result_text = response.choices[0].message.content
            
            if not result_text:
                return None
            
            json_str = result_text
            if "```json" in result_text:
                json_str = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                json_str = result_text.split("```")[1].strip()
            
            return json.loads(json_str)

        except Exception as e:
            # 返回错误信息以便记录
            print(f"\n[API Error] {e}")
            return None

class ResultsManager:
    """管理结果的保存和加载，确保线程安全"""
    def __init__(self, json_path: str, csv_path: str):
        self.json_path = json_path
        self.csv_path = csv_path
        self.lock = threading.Lock()
        self.results = self.load_existing_results()

    def load_existing_results(self) -> List[Dict]:
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def get_processed_files(self) -> Set[str]:
        return {r['filename'] for r in self.results if 'filename' in r}

    def add_result(self, result: Dict):
        with self.lock:
            self.results.append(result)
            # 每添加一个结果就保存一次，或者可以设置批量保存
            self.save_files()

    def save_files(self):
        # 保存 JSON
        try:
            with open(self.json_path, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存 JSON 失败: {e}")

        # 保存 CSV
        try:
            import csv
            with open(self.csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(['文件路径', '文件名', '分类', '摘要'])
                for r in self.results:
                    writer.writerow([
                        r.get('path', ''),
                        r.get('filename', ''),
                        r.get('category', ''),
                        r.get('summary', '')
                    ])
        except Exception as e:
            print(f"保存 CSV 失败: {e}")

def get_video_files(path: str) -> List[str]:
    video_extensions = ('.mp4', '.mov', '.avi', '.mkv', '.m4v')
    files_to_process = []
    
    if os.path.isfile(path):
        if path.lower().endswith(video_extensions):
            files_to_process.append(path)
    elif os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            for file in files:
                if file.lower().endswith(video_extensions):
                    files_to_process.append(os.path.join(root, file))
    
    return files_to_process

def process_video_task(video_path: str, classifier: AIClassifier, config: Dict, results_manager: ResultsManager, pbar):
    """单个视频处理任务"""
    try:
        filename = os.path.basename(video_path)
        
        frames = VideoProcessor.extract_frames(
            video_path, 
            max_frames=config["max_frames"], 
            target_size=config["target_size"]
        )
        
        if not frames:
            return
            
        ai_result = classifier.analyze_video_content(frames)
        
        if ai_result:
            result_entry = {
                "path": video_path,
                "filename": filename,
                "category": ai_result.get("category", "Unknown"),
                "summary": ai_result.get("summary", "No summary")
            }
            results_manager.add_result(result_entry)
            
            # 更新进度条描述
            if pbar:
                pbar.set_postfix({"Last": f"{filename[:10]}...", "Cat": result_entry['category']})
        else:
            if pbar:
                pbar.set_postfix({"Last": f"{filename[:10]}...", "Status": "Failed"})
                
    except Exception as e:
        print(f"\nError processing {video_path}: {e}")
    finally:
        if pbar:
            pbar.update(1)

def main():
    # 1. 加载配置
    config = ConfigManager.load_config()
    if not config["api_key"]:
        print("错误: 未提供有效 API Key，程序退出。")
        return

    # 2. 初始化 AI
    try:
        classifier = AIClassifier(config["api_key"], config["base_url"], config["model_name"])
    except Exception as e:
        print(f"初始化失败: {e}")
        return

    # 3. 获取输入路径
    parser = argparse.ArgumentParser(description="AI 视频内容自动识别与分类")
    parser.add_argument("path", nargs="?", help="视频文件或文件夹路径")
    args = parser.parse_args()
    
    input_path = args.path
    if not input_path:
        print("\n" + "-"*30)
        print("请拖入视频文件或文件夹到此窗口，然后按回车:")
        input_path = input(">>> ").strip().strip('"').strip("'")
    
    if not input_path or not os.path.exists(input_path):
        print("路径无效或不存在。")
        return

    # 4. 扫描文件
    all_videos = get_video_files(input_path)
    if not all_videos:
        print("未找到视频文件。")
        return

    # 5. 初始化结果管理器并排除已处理文件
    results_manager = ResultsManager(RESULTS_FILE_JSON, RESULTS_FILE_CSV)
    processed_files = results_manager.get_processed_files()
    
    videos_to_process = [v for v in all_videos if os.path.basename(v) not in processed_files]
    skipped_count = len(all_videos) - len(videos_to_process)
    
    print(f"\n总视频数: {len(all_videos)}")
    print(f"已处理: {skipped_count}")
    print(f"待处理: {len(videos_to_process)}")
    
    if not videos_to_process:
        print("所有视频均已处理。")
        return

    # 6. 多线程批量处理
    max_workers = config.get("max_workers", 4)
    print(f"开始处理，使用 {max_workers} 个线程...")
    
    # 进度条
    pbar = tqdm(total=len(videos_to_process), desc="Processing") if tqdm else None
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for video_path in videos_to_process:
            future = executor.submit(
                process_video_task, 
                video_path, 
                classifier, 
                config, 
                results_manager, 
                pbar
            )
            futures.append(future)
        
        # 等待所有任务完成
        for _ in as_completed(futures):
            pass

    if pbar:
        pbar.close()

    print("\n" + "="*30)
    print("全部处理完成！")
    print(f"结果已保存至: {RESULTS_FILE_JSON}")
    print("="*30)

if __name__ == "__main__":
    main()
