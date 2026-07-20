# Pillow (PIL) 集成方案

## 1. 目的
在 Tkinter UI 中高效加载、缩放和显示视频缩略图。

## 2. 依赖项
*   `Pillow`: 用于图像解码和缩放。
*   `tkinter.PhotoImage`: Tkinter 原生支持，但需要 Pillow 桥接以支持 JPG。

## 3. 实现细节

### 3.1 图像加载与缩放
```python
from PIL import Image, ImageTk

def load_thumbnail(path, size=(200, 150)):
    try:
        img = Image.open(path)
        img.thumbnail(size, Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception as e:
        print(f"Error loading thumbnail: {e}")
        return None
```

### 3.2 UI 集成 (侧边栏预览)
*   在工作台右侧创建一个 `ttk.Frame` 作为详情面板。
*   使用一个 `ttk.Label` 来承载 `PhotoImage`。
*   **注意**: 必须保留对 `PhotoImage` 对象的全局引用或类属性引用，否则会被垃圾回收导致图像不显示。

### 3.3 缓存机制
*   为了防止频繁从磁盘读取，可以使用简单的 `lru_cache` 或在 `VideoOrganizerGUI` 类中维护一个缩略图缓存字典。

## 4. 视频帧提取更新
*   在 `VideoProcessor` 中，将提取出的第一个帧从 `numpy.ndarray` 转换为 `PIL.Image` 并保存到 `.thumbnails` 文件夹。
*   命名规则: `MD5(video_path).jpg`。
