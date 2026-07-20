# -*- coding: utf-8 -*-
import os
import subprocess
import shutil
import sys

def check_python_version():
    major, minor = sys.version_info[:2]
    print(f"当前 Python 版本: {sys.version.split()[0]}")
    if major == 3 and minor >= 14:
        print("\n" + "!"*60)
        print("警告: 您正在使用 Python 3.14+ 进行打包。")
        print("Python 3.14 目前处于极早期开发阶段，会导致打包后的程序在其他机器上找不到 python314.dll。")
        print("强烈建议使用 Python 3.11 或 3.12 进行生产打包。")
        print("!"*60 + "\n")
        return False
    return True

def build():
    print("="*50)
    print("  Video Organizer 打包脚本 (增强兼容性版)")
    print("="*50)
    
    # 1. 环境检查
    check_python_version()
    
    try:
        import PyInstaller
        print(f"检测到 PyInstaller 版本: {PyInstaller.__version__}")
    except ImportError:
        print("错误: 未安装 PyInstaller，请先运行 'pip install pyinstaller'")
        return

    # 2. 清理旧的构建文件
    directories = ['build', 'dist']
    for d in directories:
        if os.path.exists(d):
            print(f"正在清理 {d} 目录...")
            try:
                shutil.rmtree(d)
            except Exception as e:
                print(f"警告: 无法清理 {d} : {e}")

    # 3. 执行打包
    print("\n正在执行 PyInstaller 打包流程 (使用优化后的 build_app.spec)...")
    spec_file = 'build_app.spec'
    
    if not os.path.exists(spec_file):
        print(f"错误: 找不到 {spec_file}")
        return

    # 使用 python -m PyInstaller 确保使用当前环境的 PyInstaller
    # --noconfirm 自动覆盖之前的 dist
    command = [sys.executable, '-m', 'PyInstaller', spec_file, '--noconfirm']
    
    try:
        # 使用 subprocess.run 执行命令
        result = subprocess.run(command, check=True, text=True, shell=True)
        if result.returncode == 0:
            print("\n" + "="*50)
            print("  打包成功！")
            print(f"  可执行文件位于: {os.path.abspath('dist/VideoOrganizer/VideoOrganizer.exe')}")
            print("\n  注意: 如果在其他机器运行报错 '找不到指定的模块'，请确保：")
            print("  1. 使用 Python 3.11/3.12 重新打包")
            print("  2. 目标机器安装了 VC++ Redistributable (2015-2022)")
            print("="*50)
    except subprocess.CalledProcessError as e:
        print(f"\n打包失败: {e}")
    except Exception as e:
        print(f"\n发生未知错误: {e}")

if __name__ == "__main__":
    build()
