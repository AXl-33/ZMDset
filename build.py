# -*- coding: utf-8 -*-
"""
构建 ZMDset 项目的独立可执行文件 (.exe)

使用方法:
    pip install pyinstaller
    python build.py

输出结构 (dist/):
    ZMDset.exe
    getconfig.exe
    calibrate.exe
    setConfig.json             (ZMDset + getconfig + calibrate 共用)
    resolution_config.json     (getconfig 使用)

用户将 dist/ 目录整体分发，所有 exe 和 json 在同一目录即可运行。
"""

import os
import shutil
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(PROJECT_DIR, "dist")


def build_exe(script_name, exe_name, hidden_imports=None):
    """PyInstaller 打包单个 exe 到 dist/ 根目录"""
    print(f"\n{'='*60}")
    print(f"  构建: {exe_name}.exe")
    print(f"{'='*60}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", exe_name,
        "--clean",
        "--noconfirm",
        "--distpath", DIST_DIR,
        "--workpath", os.path.join(PROJECT_DIR, "build", exe_name),
        "--specpath", os.path.join(PROJECT_DIR, "build"),
    ]
    if hidden_imports:
        for hi in hidden_imports:
            cmd.extend(["--hidden-import", hi])

    cmd.append(os.path.join(PROJECT_DIR, script_name))
    print(f"  PyInstaller: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=PROJECT_DIR, check=True)
    print(f"  ✓ {exe_name}.exe")


def main():
    print("=== ZMDset 项目构建脚本 ===\n")

    # 仅清理 build 缓存（dist 由 PyInstaller 自行管理）
    build_root = os.path.join(PROJECT_DIR, "build")
    if os.path.exists(build_root):
        try:
            shutil.rmtree(build_root)
        except PermissionError:
            pass
    os.makedirs(DIST_DIR, exist_ok=True)

    # ── 1. ZMDset（主程序，最轻量）──
    build_exe("ZMDset.py", "ZMDset",
              hidden_imports=["tkinter", "json", "collections"])

    # ── 2. getconfig（装备扫描工具）──
    build_exe("getconfig.py", "getconfig",
              hidden_imports=["tkinter", "json", "cv2", "numpy", "mss",
                              "pytesseract", "pygetwindow", "interception",
                              "PIL", "difflib", "threading"])

    # ── 3. calibrate（区域标定工具）──
    build_exe("calibrate.py", "calibrate",
              hidden_imports=["tkinter", "json", "cv2", "numpy", "mss",
                              "pytesseract", "pygetwindow", "pyautogui",
                              "PIL", "difflib"])

    # ── 复制配置文件到 dist（所有 exe 共用）──
    shared_configs = ["setConfig.json", "resolution_config.json"]
    for cf in shared_configs:
        src = os.path.join(PROJECT_DIR, cf)
        dst = os.path.join(DIST_DIR, cf)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            print(f"  ✓ {cf}")

    # ── 清理 build 缓存 ──
    build_dir = os.path.join(PROJECT_DIR, "build")
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)
        print(f"\n  已清理 build/ 缓存")

    print(f"\n{'='*60}")
    print(f"  ✅ 全部构建完成!")
    print(f"  输出目录: {DIST_DIR}")
    print(f"{'='*60}")
    print(f"\n  产物清单:")
    for f in sorted(os.listdir(DIST_DIR)):
        size_mb = os.path.getsize(os.path.join(DIST_DIR, f)) / (1024 * 1024)
        print(f"    {f:<30} {size_mb:.1f} MB")
    print(f"\n  ZMDset / getconfig / calibrate 共用 setConfig.json")


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
