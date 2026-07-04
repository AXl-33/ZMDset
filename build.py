# -*- coding: utf-8 -*-
"""
构建 ZMDset 的独立可执行文件 (.exe)

使用方法:
    pip install pyinstaller
    python build.py

输出:
    dist/ZMDset/
    ├── ZMDset.exe         (主程序)
    └── setConfig.json     (配置文件，用户可编辑)
"""

import os
import shutil
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(PROJECT_DIR, "dist", "ZMDset")


def main():
    print("=== ZMDset 构建脚本 ===")

    # 清理旧构建
    for d in ("build", "dist"):
        path = os.path.join(PROJECT_DIR, d)
        if os.path.exists(path):
            shutil.rmtree(path)

    # PyInstaller 打包
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", "ZMDset",
        "--clean",
        "--noconfirm",
        os.path.join(PROJECT_DIR, "ZMDset.py"),
    ]
    print(f"执行: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=PROJECT_DIR, check=True)

    # 复制配置文件到输出目录
    src_config = os.path.join(PROJECT_DIR, "setConfig.json")
    dst_config = os.path.join(DIST_DIR, "setConfig.json")
    if os.path.exists(src_config):
        shutil.copy2(src_config, dst_config)
        print(f"已复制: setConfig.json -> {dst_config}")

    # 清理临时文件
    spec_file = os.path.join(PROJECT_DIR, "ZMDset.spec")
    if os.path.exists(spec_file):
        os.remove(spec_file)

    print(f"\n✅ 构建完成! 输出目录: {DIST_DIR}")
    print(f"   可分发文件: ZMDset.exe + setConfig.json")


if __name__ == "__main__":
    main()
