# -*- coding: utf-8 -*-
"""
Video Organizer Pro (PySide6 Refactor - Modular Version)
This file now acts as a launcher for the modularized application.
The core logic has been moved to gui/ and core/ directories.
"""

import sys
import os

# 确保当前目录在路径中
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from video_organizer_app import main

if __name__ == "__main__":
    main()
