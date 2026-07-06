#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZMDset — 游戏装备自动识别工具 (getconfig)

功能：
  1. 捕获游戏窗口截图（mss）
  2. 图像处理检测仓库装备网格（OpenCV 二值化 + 轮廓检测）
  3. 自动点击遍历装备（pyautogui）
  4. 识别装备属性等级（蓝色斜线计数 → a/b/c 值）
  5. OCR 识别装备名称（Tesseract，可选）
  6. 生成临时配置，审核后合并到 setConfig.json

设计要点：
  - 单轮扫描中分辨率不变，属性区域坐标在配置中写死
  - 网格通过图像处理动态检测（适应窗口位置微调）
  - 每页扫描完成后自动停止，等待用户手动翻页
  - 支持多分辨率切换（用户从预设列表中选择）

依赖安装：
  pip install opencv-python numpy mss pyautogui pillow pytesseract

用法：
  python getconfig.py          # 启动 GUI
  python getconfig.py --cli    # 命令行模式（开发中）
"""

import json
import os
import sys
import time
import threading
from difflib import SequenceMatcher
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from collections import OrderedDict
from datetime import datetime

import cv2
import numpy as np

# ── 可选依赖 ─────────────────────────────────────────────
try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

try:
    import pyautogui
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False

try:
    import pygetwindow as gw
    HAS_PYGETWINDOW = True
except ImportError:
    HAS_PYGETWINDOW = False

try:
    import keyboard
    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False

try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

# ============================================================
#  工具函数
# ============================================================

def _app_dir():
    """返回应用程序所在目录（兼容 PyInstaller 打包）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _res_cfg_path():
    return os.path.join(_app_dir(), "resolution_config.json")


def _set_cfg_path():
    return os.path.join(_app_dir(), "setConfig.json")


# ============================================================
#  分辨率配置管理
# ============================================================

class ResolutionConfig:
    """加载并管理分辨率配置"""

    def __init__(self, filepath=None):
        self.filepath = filepath or _res_cfg_path()
        self.data = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.filepath):
            raise FileNotFoundError(f"分辨率配置文件不存在: {self.filepath}")
        with open(self.filepath, "r", encoding="utf-8") as f:
            self.data = json.load(f)

    @property
    def resolutions(self):
        """返回所有预设分辨率列表"""
        return [k for k in self.data.keys() if not k.startswith("_")]

    def get(self, resolution):
        """获取指定分辨率的配置，不存在返回 None"""
        return self.data.get(resolution)

    def save(self):
        """保存配置到文件"""
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=4)


# ============================================================
#  屏幕捕获模块（mss）
# ============================================================

class WindowCapture:
    """捕获指定窗口或屏幕区域的截图"""

    def __init__(self):
        if not HAS_MSS:
            raise ImportError("请安装 mss: pip install mss")
        self.sct = mss.MSS()
        self.monitor = None  # 由 set_region 设置

    def set_region(self, left, top, width, height):
        """设置捕获区域（屏幕绝对坐标）"""
        self.monitor = {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        }

    def set_fullscreen(self, monitor_index=1):
        """捕获整个屏幕"""
        self.monitor = self.sct.monitors[monitor_index]

    def capture(self):
        """捕获一帧，返回 BGR numpy 数组 (OpenCV 格式)"""
        if self.monitor is None:
            raise RuntimeError("请先调用 set_region 或 set_fullscreen")
        img = self.sct.grab(self.monitor)
        # mss 返回 BGRA，转为 BGR
        arr = np.array(img)
        return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)

    def capture_region(self, left, top, width, height):
        """捕获指定区域（屏幕绝对坐标），返回 BGR"""
        monitor = {"left": left, "top": top, "width": width, "height": height}
        img = self.sct.grab(monitor)
        arr = np.array(img)
        return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)


# ============================================================
#  网格检测模块（OpenCV）
# ============================================================

class GridDetector:
    """
    检测游戏仓库中的装备图标网格。
    1. 裁剪网格区域（grid.br_x/y）
    2. 自适应阈值 → 腐蚀膨胀去噪 → 找轮廓 → 过滤方形 → boundingRect 中心点
    3. 按行列排序 → 补全跳过的格子（末行右侧空缺不补）
    4. 回退：硬编码 cells 坐标表
    """

    def __init__(self, grid_cfg):
        self.cfg = grid_cfg
        self.cols = grid_cfg["cols"]
        self.rows_visible = grid_cfg.get("rows_visible", 6)
        self.grid_x = grid_cfg["x"]
        self.grid_y = grid_cfg["y"]
        self.grid_br_x = grid_cfg.get("br_x", 9999)
        self.grid_br_y = grid_cfg.get("br_y", 9999)
        self.cell_size = grid_cfg.get("cell_size", 70)

    def detect(self, frame_bgr):
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        # 裁剪网格区域（用 br_x/br_y 限定右下角）
        h, w = gray.shape
        x2 = min(w, self.grid_br_x)
        y2 = min(h, self.grid_br_y)
        if x2 > self.grid_x and y2 > self.grid_y:
            gray = gray[self.grid_y:y2, self.grid_x:x2]

        # 自适应阈值
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 31, 5)

        # 形态学：先腐蚀去除小块噪点，再膨胀恢复格子
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        binary = cv2.erode(binary, kernel, iterations=1)
        binary = cv2.dilate(binary, kernel, iterations=1)

        cells = self._find_cells(binary)
        cells = self._offset_back(cells)   # 坐标还原到窗口坐标系

        # 不够 → 硬编码坐标表
        if len(cells) < self.cols:
            hc = self._hardcoded_cells()
            if hc:
                return hc

        # 排序 + 补全跳过的格子
        return self._sort_and_fill(cells)

    def _find_cells(self, binary):
        """从二值图中提取方形格子"""
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        tolerance = 0.35
        min_s = int(self.cell_size * (1 - tolerance))
        max_s = int(self.cell_size * (1 + tolerance))

        cells = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if not (min_s < w < max_s and min_s < h < max_s):
                continue
            if not (0.7 < w / h < 1.4):
                continue
            if cv2.contourArea(cnt) / (w * h) < 0.5:
                continue
            cx, cy = x + w // 2, y + h // 2
            cells.append((x, y, w, h, cx, cy))
        return cells

    def _offset_back(self, cells):
        """将裁剪后的坐标还原到窗口坐标系"""
        if not cells:
            return cells
        ox, oy = self.grid_x, self.grid_y
        return [(x + ox, y + oy, w, h, cx + ox, cy + oy) for (x, y, w, h, cx, cy) in cells]

    def _sort_and_fill(self, cells):
        """按行列排序，补全跳过的格子（末行右侧空缺不补）"""
        if not cells:
            return []

        # 按行分组
        sorted_by_y = sorted(cells, key=lambda c: c[5])
        rows = []
        current = [sorted_by_y[0]]
        for cell in sorted_by_y[1:]:
            if abs(cell[5] - current[-1][5]) < self.cell_size * 0.5:
                current.append(cell)
            else:
                rows.append(sorted(current, key=lambda c: c[4]))
                current = [cell]
        rows.append(sorted(current, key=lambda c: c[4]))

        # 逐行补全跳过的格子
        result = []
        for row_idx, row_cells in enumerate(rows):
            last_row = (row_idx == len(rows) - 1)
            filled = self._fill_row_gaps(row_cells, row_idx, last_row)
            for col_idx, (cx, cy) in enumerate(filled):
                result.append((col_idx, row_idx, cx, cy))
        return result

    def _fill_row_gaps(self, row_cells, row_idx, is_last_row):
        """补全一行中跳过的格子，末行右侧不补"""
        if len(row_cells) <= 1:
            return [(c[4], c[5]) for c in row_cells]

        pts = [(c[4], c[5]) for c in row_cells]
        gaps = [pts[i + 1][0] - pts[i][0] for i in range(len(pts) - 1)]
        avg_step = sum(gaps) / len(gaps) if gaps else self.cell_size + self.cfg.get("gap", 4)

        filled = [pts[0]]
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            dist = x2 - x1
            while dist > avg_step * 1.4:
                x1 += avg_step
                if is_last_row and len(filled) >= self.cols:
                    break
                filled.append((int(x1), int((y1 + y2) / 2)))
                dist = x2 - x1
            filled.append(pts[i + 1])

        return filled

    def _hardcoded_cells(self):
        """从配置读取硬编码的格子中心坐标"""
        pts = self.cfg.get("cells")
        if not pts:
            return None
        result = []
        for idx, (cx, cy) in enumerate(pts):
            result.append((idx % self.cols, idx // self.cols, cx, cy))
        return result


# ============================================================
#  属性识别模块（蓝色斜线检测）
# ============================================================

class AttributeRecognizer:
    """
    识别装备详情面板中的属性等级。
    原理：蓝色斜线代表等级（// 表示2级，/// 表示3级）。
    每条属性有 0~3 条斜线，装备通常有 3 条属性（少数2条）。

    方法：HSV 蓝色过滤 → 将属性区域按行分割 → 每行按列三等分 →
          统计每个格子的蓝色像素 → 超过阈值认为存在斜线 → 计数
    """

    def __init__(self, detail_cfg, color_cfg):
        """
        detail_cfg: resolution_config.json 中 resolution.detail 节
        color_cfg:  resolution_config.json 中 resolution.color 节
        """
        self.attr_x = detail_cfg["attr_x"]
        self.attr_y = detail_cfg["attr_y"]
        self.attr_w = detail_cfg["attr_w"]
        self.attr_h = detail_cfg["attr_h"]

        self.blue_lower = np.array(color_cfg["blue_lower"])
        self.blue_upper = np.array(color_cfg["blue_upper"])
        self.slash_min_pixels = color_cfg.get("slash_min_pixels", 15)

    def recognize(self, frame_bgr, num_attrs=3):
        """
        从截图中识别属性等级。
        frame_bgr: 游戏窗口截图的 BGR numpy 数组
        num_attrs: 预期属性条数（通常为 3，少数装备为 2）
        返回: [a, b, c] 各属性等级（0-3），如 [2, 3, 1] 表示 a=2, b=3, c=1
        """
        # 裁剪属性区域
        h, w = frame_bgr.shape[:2]
        x1 = max(0, self.attr_x)
        y1 = max(0, self.attr_y)
        x2 = min(w, self.attr_x + self.attr_w)
        y2 = min(h, self.attr_y + self.attr_h)

        if x2 <= x1 or y2 <= y1:
            return [0, 0, 0]

        attr_roi = frame_bgr[y1:y2, x1:x2]

        # HSV 蓝色过滤
        hsv = cv2.cvtColor(attr_roi, cv2.COLOR_BGR2HSV)
        blue_mask = cv2.inRange(hsv, self.blue_lower, self.blue_upper)

        # 形态学去噪
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
        blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_OPEN, kernel, iterations=1)

        roi_h, roi_w = blue_mask.shape
        row_h = max(1, roi_h // num_attrs)

        levels = []
        for i in range(num_attrs):
            row_start = i * row_h
            row_end = min((i + 1) * row_h, roi_h)
            row_slice = blue_mask[row_start:row_end, :]

            # 三列检测：每列是否有足够蓝色像素
            col_w = roi_w // 3
            slash_count = 0
            for j in range(3):
                col_start = j * col_w
                col_end = min((j + 1) * col_w, roi_w)
                zone = row_slice[:, col_start:col_end]
                blue_pixels = np.sum(zone) // 255
                if blue_pixels >= self.slash_min_pixels:
                    slash_count += 1

            # 双重验证：如果列检测为 0 或 3，用整体密度再确认
            if slash_count == 0:
                total_blue = np.sum(row_slice) // 255
                if total_blue > self.slash_min_pixels * 0.6:
                    slash_count = 1  # 至少1级
            elif slash_count == 3:
                # 确认每列确实都有显著蓝色（防止噪点误判）
                for j in range(3):
                    col_start = j * col_w
                    col_end = min((j + 1) * col_w, roi_w)
                    zone = row_slice[:, col_start:col_end]
                    if np.sum(zone) // 255 < self.slash_min_pixels * 0.5:
                        slash_count = 2
                        break

            levels.append(min(slash_count, 3))

        return levels

    def debug_visualize(self, frame_bgr, num_attrs=3):
        """返回用于调试的可视化图像（标注属性区域和识别结果）"""
        viz = frame_bgr.copy()
        h, w = viz.shape[:2]
        x1, y1 = self.attr_x, self.attr_y
        x2, y2 = min(w, x1 + self.attr_w), min(h, y1 + self.attr_h)

        # 画属性区域框
        cv2.rectangle(viz, (x1, y1), (x2, y2), (0, 255, 0), 2)

        # 画行分割线
        roi_h = y2 - y1
        row_h = max(1, roi_h // num_attrs)
        for i in range(1, num_attrs):
            ly = y1 + i * row_h
            cv2.line(viz, (x1, ly), (x2, ly), (0, 255, 255), 1)

        return viz


# ============================================================
#  装备名称 OCR 模块（Tesseract）
# ============================================================

# 装备名称中出现的所有字符（中文 + 英文 + 数字 + 符号）
_NAME_CHARS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789·."
    "壹贰叁肆伍陆柒捌玖拾型点剑潮涌脉冲生物辅助拓荒长息壤流落潮"
    "旧锋碾骨清波纾难应龙动火悬河超域护甲护手护服手甲手套手环"
    "工具组瞄具护板盾针接驳器校准器水罐印章供养栓供氧栓通信器"
    "定位仪电力匣测温镜辅助臂蓄电核竹刃火石短棍腕带面具披巾"
    "臂甲重甲轻甲胸甲装甲刺刃短刃定位信标雷达"
    "ＭＩ"  # 全角字母（游戏可能使用）
)

# 自动检测 Tesseract 安装路径
def _find_tesseract():
    """查找 Tesseract-OCR 安装路径"""
    import subprocess
    common_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p
    # 尝试系统 PATH
    try:
        result = subprocess.run(["where", "tesseract"], capture_output=True, text=True)
        if result.returncode == 0:
            path = result.stdout.strip().split("\n")[0]
            if os.path.exists(path):
                return path
    except Exception:
        pass
    return None

TESSERACT_PATH = _find_tesseract() if HAS_TESSERACT else None


class NameRecognizer:
    """OCR 识别装备名称 + 模糊匹配已有装备名"""

    def __init__(self, detail_cfg, equipment_names=None):
        self.name_x = detail_cfg["name_x"]
        self.name_y = detail_cfg["name_y"]
        self.name_w = detail_cfg["name_w"]
        self.name_h = detail_cfg["name_h"]
        self.ocr_threshold = detail_cfg.get("ocr_threshold", 150)
        self._available = HAS_TESSERACT and TESSERACT_PATH is not None
        self._names = equipment_names or []
        if HAS_TESSERACT and TESSERACT_PATH:
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

    def recognize(self, frame_bgr):
        """
        从截图中 OCR 识别装备名称，然后与已有装备名模糊匹配。
        返回: 匹配到的名称或 OCR 原始结果，失败返回 ""
        """
        if not self._available:
            return ""

        h, w = frame_bgr.shape[:2]
        x1 = max(0, self.name_x)
        y1 = max(0, self.name_y)
        x2 = min(w, self.name_x + self.name_w)
        y2 = min(h, self.name_y + self.name_h)

        if x2 <= x1 or y2 <= y1:
            return ""

        roi = frame_bgr[y1:y2, x1:x2]

        # 预处理：放大 3 倍提升小字识别率
        roi = cv2.resize(roi, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        # 暗底白字 → 反色为白底黑字（Tesseract 最佳输入）
        gray = cv2.bitwise_not(gray)
        # 左侧 2/3 区域计算 OTSU 阈值，全图应用（右侧可能有 UI 干扰）
        h_g, w_g = gray.shape
        left_w = w_g * 2 // 3
        thresh_val, _ = cv2.threshold(
            gray[:, :left_w], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        _, binary = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY)

        # Tesseract 识别
        _BLACKLIST = "0123456789.,;:!?()[]{}'\"`~@#$%^&*_-+=/\\|<>·"
        try:
            text = pytesseract.image_to_string(
                binary,
                lang="chi_sim",
                config=f"--psm 7 -c tessedit_char_blacklist={_BLACKLIST}"
            )
            raw = text.strip().replace(" ", "").replace("\n", "").replace("\r", "")
            if raw in ("暂缺", "", "—"):
                return ""
        except Exception:
            return ""

        # 模糊匹配已有装备名
        return self._best_match(raw)

    def _best_match(self, raw_name):
        """在已有装备名中找最相似的，>=60% 采纳，否则返回 OCR 原始结果"""
        if not raw_name or not self._names:
            return raw_name
        best, best_score = None, 0
        for c in self._names:
            score = SequenceMatcher(None, raw_name, c).ratio()
            if score > best_score:
                best_score = score
                best = c
        return best if best_score >= 0.6 else raw_name


# ============================================================
#  鼠标控制模块 —— Interception 驱动级模拟（内核层）
# ============================================================

# 全局初始化（驱动只允许一个连接，多次调用会失败）
_interception = None
_INTERCEPTION_OK = False
try:
    import interception
    interception.auto_capture_devices()
    _interception = interception
    _INTERCEPTION_OK = True
    print("[Interception] 驱动初始化成功", flush=True)
except Exception as e:
    print(f"[Interception] 初始化失败: {e}", flush=True)


class MouseController:
    """通过 Interception 驱动在硬件层模拟鼠标输入"""

    def click(self, screen_x, screen_y, button="left"):
        if not _INTERCEPTION_OK:
            return
        _interception.move_to(int(screen_x), int(screen_y))
        _interception.click()

    def activate_window(self):
        pass


# ============================================================
#  装备扫描编排器
# ============================================================

class EquipmentScanner:
    """
    编排整个扫描流程：
    1. 截图
    2. 检测网格
    3. 逐个点击格子
    4. 每次点击后截图识别属性
    5. 收集结果
    """

    def __init__(self, resolution, res_cfg: ResolutionConfig, on_log=None, on_progress=None, on_cell_done=None):
        self.resolution = resolution
        self.cfg = res_cfg.get(resolution)
        if self.cfg is None:
            raise ValueError(f"未找到分辨率配置: {resolution}")

        self.capture = WindowCapture()
        self.grid_detector = GridDetector(self.cfg["grid"])
        self.attr_recognizer = AttributeRecognizer(self.cfg["detail"], self.cfg["color"])
        self.name_recognizer = NameRecognizer(self.cfg["detail"], self.cfg.get("equipment_names"))
        self.mouse = MouseController()

        # 回调
        self.on_log = on_log or (lambda msg: None)
        self.on_progress = on_progress or (lambda cur, total: None)
        self.on_cell_done = on_cell_done or (lambda idx, name, stats: None)

        # 扫描状态
        self.scanning = False
        self.paused = False
        self.results = []
        self.window_offset = (0, 0)
        self._window_title = None  # 如果通过标题定位，可动态追踪窗口位置

    def set_window_region(self, left, top, width, height, window_title=None):
        """设置游戏窗口在屏幕上的区域；若提供 window_title 则后续每页自动追踪位置"""
        self.capture.set_region(left, top, width, height)
        self.window_offset = (left, top)
        self._window_title = window_title

    def _refresh_window_position(self):
        """通过窗口标题重新查询位置（支持移动窗口）"""
        if not self._window_title or not HAS_PYGETWINDOW:
            return False
        try:
            windows = gw.getWindowsWithTitle(self._window_title)
            if not windows:
                return False
            w = windows[0]
            if w.isMinimized:
                try:
                    w.restore()
                    time.sleep(0.2)
                except Exception:
                    pass
            new_left, new_top = w.left, w.top
            new_w, new_h = w.width, w.height
            old_left, old_top = self.window_offset
            if (new_left, new_top) != (old_left, old_top):
                self._log(f"🪟 窗口位置已变化: ({old_left},{old_top}) → ({new_left},{new_top})")
            self.window_offset = (new_left, new_top)
            self.capture.set_region(new_left, new_top, new_w, new_h)
            return True
        except Exception:
            return False

    def _to_screen(self, win_x, win_y):
        """窗口内坐标 → 屏幕绝对坐标"""
        return (self.window_offset[0] + win_x, self.window_offset[1] + win_y)

    def _log(self, msg):
        self.on_log(msg)

    def scan_page(self):
        """
        扫描当前可见的一页装备。
        返回: results list，每个元素 {"name": str, "a": int, "b": int, "c": int}
        """
        if not self.scanning:
            return []

        self._log("📸 正在截图...")
        frame = self.capture.capture()
        self._log(f"   截图尺寸: {frame.shape[1]}x{frame.shape[0]}")

        # 检测网格
        self._log("🔍 正在检测装备格...")
        cells = self.grid_detector.detect(frame)
        if not cells:
            self._log("⚠️ 未检测到装备格，使用预设坐标推算")
            cells = self.grid_detector._fallback_grid()

        total = len(cells)
        self._log(f"   检测到 {total} 个装备格 ({self.cfg['grid']['cols']}列 × {total // max(1, self.cfg['grid']['cols'])}行)")
        if self.name_recognizer._available:
            self._log("🔤 OCR 名称识别: 已启用")
        else:
            self._log("🔤 OCR 名称识别: 未启用（将使用占位名）")

        page_results = []
        for idx, (col, row, cx, cy) in enumerate(cells):
            if not self.scanning:
                break

            scr_x, scr_y = self._to_screen(cx, cy)

            # 点击装备格
            self._log(f"🖱️ 点击格子 [{row},{col}] → 屏幕坐标 ({scr_x}, {scr_y})")
            try:
                self.mouse.click(scr_x, scr_y)
            except Exception as e:
                self._log(f"   ❌ 点击异常: {e}")
                continue

            # 等待 UI 刷新
            time.sleep(0.3)

            # 截图 → 识别属性
            frame_detail = self.capture.capture()
            stats = self.attr_recognizer.recognize(frame_detail, num_attrs=3)

            # OCR 装备名称
            name = self.name_recognizer.recognize(frame_detail)
            if not name:
                name = f"装备_{row}_{col}"
                self._log(f"   ⚠️ OCR 未识别到名称，使用占位名")

            item = {"name": name, "a": stats[0], "b": stats[1], "c": stats[2]}
            page_results.append(item)
            self.results.append(item)

            self._log(f"   ✅ [{row},{col}] {name}: a={stats[0]} b={stats[1]} c={stats[2]}")
            self.on_cell_done(idx, name, stats)
            self.on_progress(idx + 1, total)

        return page_results

    def start_scan(self, window_left, window_top, window_width, window_height, window_title=None):
        """启动扫描（在新线程中运行）"""
        self.set_window_region(window_left, window_top, window_width, window_height, window_title)
        self.scanning = True
        self.paused = False
        self.results.clear()

        thread = threading.Thread(target=self._scan_loop, daemon=True)
        thread.start()
        return thread

    def _scan_loop(self):
        """扫描主循环"""
        page_num = 1
        while self.scanning:
            if self.paused:
                time.sleep(0.2)
                continue

            self._log(f"\n{'='*50}")
            self._log(f"📄 第 {page_num} 页扫描开始")
            self._log(f"{'='*50}")

            # 每页扫描前刷新窗口位置（支持窗口移动）
            self._refresh_window_position()

            page_results = self.scan_page()

            self._log(f"\n📄 第 {page_num} 页完成！共识别 {len(page_results)} 件装备。")
            self._log("⏸️  扫描已暂停，请手动翻页后点击「继续」按钮。")

            # 自动暂停，等待用户操作
            self.paused = True
            page_num += 1

    def stop(self):
        """停止扫描"""
        self.scanning = False
        self.paused = False

    def resume(self):
        """继续扫描（翻页后）"""
        self.paused = False

    def get_summary(self):
        """获取扫描结果摘要"""
        return self.results[:]


# ============================================================
#  配置合并模块
# ============================================================

class ConfigMerger:
    """将扫描结果合并到 setConfig.json"""

    def __init__(self, setconfig_path=None):
        self.setconfig_path = setconfig_path or _set_cfg_path()

    def load_existing(self):
        """加载现有配置"""
        if not os.path.exists(self.setconfig_path):
            return {"characters": {}, "teams": {}, "equipment": {}}
        with open(self.setconfig_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def generate_temp_config(self, scan_results):
        """
        根据扫描结果生成临时 equipment 配置。
        scan_results: [{"name": str, "a": int, "b": int, "c": int}, ...]
        返回临时配置的 dict（只包含 equipment 部分）
        """
        equipment = OrderedDict()
        for item in scan_results:
            name = item["name"]
            if not name or name.startswith("装备_"):
                continue
            a, b, c = item["a"], item["b"], item["c"]
            if name not in equipment:
                equipment[name] = []
            equipment[name].append({"a": a, "b": b, "c": c})

        # 对每个装备的多条记录排序
        for name in equipment:
            equipment[name].sort(key=lambda x: x["a"] + x["b"] + x["c"], reverse=True)

        return {"equipment": equipment}

    def merge(self, scan_results, dry_run=True):
        """
        合并扫描结果到 setConfig.json。
        dry_run=True: 只生成临时配置，不实际写入
        dry_run=False: 直接写入
        返回临时/合并后的配置
        """
        existing = self.load_existing()
        temp_eq = self.generate_temp_config(scan_results)

        if dry_run:
            # 返回仅包含新扫描部分的临时配置
            return temp_eq

        # 合并：更新 equipment 部分（保留已有，新增/覆盖扫描到的）
        if "equipment" not in existing:
            existing["equipment"] = {}
        for name, stats_list in temp_eq.get("equipment", {}).items():
            existing["equipment"][name] = stats_list

        # 写入文件（保留 characters 和 teams 不变）
        with open(self.setconfig_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=4)

        return existing

    def save_temp(self, scan_results, filepath):
        """将临时配置保存到指定文件"""
        temp = self.generate_temp_config(scan_results)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(temp, f, ensure_ascii=False, indent=4)


# ============================================================
#  GUI 主界面
# ============================================================

class ScannerApp:
    """图形化扫描工具"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ZMDset — 装备自动识别工具")
        self.root.minsize(700, 550)

        # ── 加载分辨率配置 ──
        try:
            self.res_cfg = ResolutionConfig()
        except FileNotFoundError as e:
            messagebox.showerror("错误", str(e))
            sys.exit(1)

        # ── 扫描器（初始为 None，选择分辨率后创建） ──
        self.scanner: EquipmentScanner = None
        self.merger = ConfigMerger()

        # ── 状态变量 ──
        self.scan_results = []  # 本轮累计结果
        self.window_region = None  # (left, top, width, height)
        self._window_title = None  # 窗口标题（用于动态追踪位置）
        self._preview_img = None  # 预览截图

        # ── 构建界面 ──
        self._build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._register_app_hotkeys()
        self.root.mainloop()

    # ── UI 构建 ──────────────────────────────────────────

    def _build_ui(self):
        """构建主界面"""
        # 顶部控制栏
        ctrl_frame = ttk.Frame(self.root, padding=10)
        ctrl_frame.pack(fill=tk.X)

        # 分辨率选择
        ttk.Label(ctrl_frame, text="游戏分辨率:").pack(side=tk.LEFT, padx=(0, 5))
        self.res_var = tk.StringVar(value=self.res_cfg.resolutions[0] if self.res_cfg.resolutions else "")
        res_combo = ttk.Combobox(ctrl_frame, textvariable=self.res_var,
                                 values=self.res_cfg.resolutions, state="readonly", width=12)
        res_combo.pack(side=tk.LEFT, padx=(0, 15))

        # 窗口定位按钮
        ttk.Button(ctrl_frame, text="📍 定位游戏窗口", command=self._locate_window).pack(side=tk.LEFT, padx=5)

        # 扫描控制按钮
        self.btn_scan = ttk.Button(ctrl_frame, text="▶ 开始扫描", command=self._start_scan, state=tk.DISABLED)
        self.btn_scan.pack(side=tk.LEFT, padx=5)

        self.btn_resume = ttk.Button(ctrl_frame, text="▶ 继续下一页", command=self._resume_scan, state=tk.DISABLED)
        self.btn_resume.pack(side=tk.LEFT, padx=5)

        self.btn_stop = ttk.Button(ctrl_frame, text="⏹ 停止", command=self._stop_scan, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=5)

        # 右侧按钮
        ttk.Button(ctrl_frame, text="👁 预览属性区域", command=self._preview_attr).pack(side=tk.RIGHT, padx=5)
        ttk.Button(ctrl_frame, text="💾 导出临时配置", command=self._export_temp).pack(side=tk.RIGHT, padx=5)
        self.btn_merge = ttk.Button(ctrl_frame, text="✅ 确认合并到 setConfig.json",
                                    command=self._confirm_merge, state=tk.DISABLED)
        self.btn_merge.pack(side=tk.RIGHT, padx=5)

        # 中间区域：左右分栏
        main_pw = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_pw.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        # 左侧：识别结果列表
        left_frame = ttk.LabelFrame(main_pw, text="扫描结果", padding=5)
        main_pw.add(left_frame, weight=1)

        list_toolbar = ttk.Frame(left_frame)
        list_toolbar.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(list_toolbar, text="清空结果", command=self._clear_results).pack(side=tk.RIGHT)
        self.lbl_total = ttk.Label(list_toolbar, text="共 0 件装备")
        self.lbl_total.pack(side=tk.LEFT)

        # 结果 Treeview
        tree_frame = ttk.Frame(left_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self.result_tree = ttk.Treeview(tree_frame,
            columns=("名称", "a", "b", "c"), show="headings", height=15)
        self.result_tree.heading("名称", text="装备名称")
        self.result_tree.heading("a", text="属性a")
        self.result_tree.heading("b", text="属性b")
        self.result_tree.heading("c", text="属性c")
        self.result_tree.column("名称", width=180, anchor="w")
        self.result_tree.column("a", width=60, anchor="center")
        self.result_tree.column("b", width=60, anchor="center")
        self.result_tree.column("c", width=60, anchor="center")

        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=tree_scroll.set)
        self.result_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 双击编辑单元格
        self.result_tree.bind("<Double-1>", self._on_tree_double_click)
        # 右键删除
        self.result_tree.bind("<Delete>", lambda e: self._delete_selected())

        # 右侧：日志面板
        right_frame = ttk.LabelFrame(main_pw, text="运行日志", padding=5)
        main_pw.add(right_frame, weight=1)

        self.log_text = tk.Text(right_frame, wrap=tk.WORD, state=tk.DISABLED,
                                font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
                                insertbackground="white")
        log_scroll = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 底部进度条
        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.progress.pack(fill=tk.X, padx=10, pady=(0, 5))

        # 状态栏
        self.status_var = tk.StringVar(value="就绪 — 请先定位游戏窗口")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    # ── 日志 ────────────────────────────────────────────

    def _log(self, msg):
        """追加日志"""
        self.log_text.configure(state=tk.NORMAL)
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ── 窗口定位 ────────────────────────────────────────

    def _locate_window(self):
        """弹出窗口定位对话框 —— 通过窗口标题查找"""
        dialog = tk.Toplevel(self.root)
        dialog.title("定位游戏窗口")
        dialog.geometry("500x320")
        dialog.minsize(450, 280)
        dialog.transient(self.root)
        dialog.grab_set()

        # ── 窗口标题查找 ──
        title_frame = ttk.LabelFrame(dialog, text="按窗口标题查找游戏窗口", padding=10)
        title_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(10, 5))

        ttk.Label(title_frame, text="输入游戏窗口标题关键词（支持模糊匹配）:",
                  wraplength=450).pack(anchor="w")

        search_row = ttk.Frame(title_frame)
        search_row.pack(fill=tk.X, pady=(8, 0))
        self.entry_title = ttk.Entry(search_row, width=28)
        self.entry_title.pack(side=tk.LEFT, padx=(0, 5))
        self.entry_title.bind("<Return>", lambda e: self._search_windows(dialog))
        ttk.Button(search_row, text="🔍 搜索窗口",
                   command=lambda: self._search_windows(dialog)).pack(side=tk.LEFT, padx=2)
        ttk.Button(search_row, text="🔄 列出全部",
                   command=lambda: self._list_all_windows(dialog)).pack(side=tk.LEFT, padx=2)

        # 搜索结果列表
        list_frame = ttk.Frame(title_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.win_listbox = tk.Listbox(list_frame, height=6, font=("Consolas", 9))
        win_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.win_listbox.yview)
        self.win_listbox.configure(yscrollcommand=win_scroll.set)
        self.win_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        win_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.win_listbox.bind("<<ListboxSelect>>", lambda e: self._on_window_selected())
        self.win_listbox.bind("<Double-Button-1>", lambda e: self._confirm_window(dialog))

        # 选中窗口信息
        self.lbl_win_info = ttk.Label(title_frame, text="选中窗口: —", foreground="gray")
        self.lbl_win_info.pack(anchor="w", pady=(5, 0))

        ttk.Button(title_frame, text="✅ 确认使用选中窗口",
                   command=lambda: self._confirm_window(dialog)).pack(pady=(8, 0))

        # ── 底部提示 ──
        ttk.Label(dialog, text="💡 提示：通过标题定位后，扫描过程中可以随意移动游戏窗口，程序会自动追踪位置",
                  foreground="#888888", font=("Microsoft YaHei UI", 8),
                  wraplength=460, anchor="center").pack(pady=(5, 8))

        self._cached_windows = []
        # 初始列出所有窗口
        dialog.after(300, lambda: self._list_all_windows(dialog))

    # ── 窗口搜索 ──────────────────────────────────────

    def _search_windows(self, dialog):
        """按标题搜索窗口"""
        if not HAS_PYGETWINDOW:
            messagebox.showerror("错误", "pygetwindow 未安装", parent=dialog)
            return
        keyword = self.entry_title.get().strip()
        if not keyword:
            self._list_all_windows(dialog)
            return
        windows = gw.getWindowsWithTitle(keyword)
        self._cached_windows = windows
        self._populate_win_list(windows)

    def _list_all_windows(self, dialog):
        """列出所有可见窗口"""
        if not HAS_PYGETWINDOW:
            messagebox.showerror("错误", "pygetwindow 未安装", parent=dialog)
            return
        windows = [w for w in gw.getAllWindows() if w.title.strip() and w.width > 100 and w.height > 100]
        # 按标题排序
        windows.sort(key=lambda w: w.title)
        self._cached_windows = windows
        self._populate_win_list(windows)

    def _populate_win_list(self, windows):
        """填充窗口列表"""
        self.win_listbox.delete(0, tk.END)
        with_icon = "🖥" if os.name == "nt" else "🪟"
        for w in windows:
            title = w.title[:60]
            line = f"{with_icon} {title:<62} {w.width}×{w.height}  @({w.left},{w.top})"
            self.win_listbox.insert(tk.END, line)

    def _on_window_selected(self):
        """选中窗口时的信息更新"""
        sel = self.win_listbox.curselection()
        if not sel or not self._cached_windows:
            return
        idx = sel[0]
        if idx < len(self._cached_windows):
            w = self._cached_windows[idx]
            self.lbl_win_info.configure(
                text=f"选中: 「{w.title[:40]}」 位置({w.left},{w.top}) 大小{w.width}×{w.height}",
                foreground="#0066cc")

    def _confirm_window(self, dialog):
        """确认使用选中的窗口"""
        sel = self.win_listbox.curselection()
        if not sel or not self._cached_windows:
            messagebox.showinfo("提示", "请先在列表中选择一个窗口", parent=dialog)
            return
        idx = sel[0]
        if idx >= len(self._cached_windows):
            return
        w = self._cached_windows[idx]

        # 如果窗口最小化，尝试恢复
        if w.isMinimized:
            try:
                w.restore()
                time.sleep(0.3)
                # 重新获取位置（恢复后可能变化）
                w = self._cached_windows[idx]
            except Exception:
                pass

        left, top = w.left, w.top
        width, height = w.width, w.height

        # 验证窗口大小是否与所选分辨率匹配
        res = self.res_var.get()
        if res and "x" in res:
            rw, rh = res.split("x")
            if abs(width - int(rw)) > 100 or abs(height - int(rh)) > 100:
                answer = messagebox.askyesno(
                    "大小不匹配",
                    f"窗口实际大小 ({width}×{height}) 与所选分辨率 ({rw}×{rh}) 差异较大。\n\n"
                    f"是否仍要使用实际窗口大小？\n"
                    f"（选「是」使用实际大小，选「否」返回重选）",
                    parent=dialog)
                if not answer:
                    return

        self.window_region = (left, top, width, height)
        self._window_title = w.title  # 保存标题，扫描时动态追踪位置
        self._log(f"📍 窗口已定位: 「{w.title[:30]}」 左上({left},{top}) 大小 {width}×{height}  [位置动态追踪]")
        self.status_var.set(f"窗口已定位: {w.title[:25]} ({left},{top}) {width}×{height}")
        self.btn_scan.configure(state=tk.NORMAL)
        dialog.destroy()

    # ── 扫描控制 ────────────────────────────────────────

    def _start_scan(self):
        """开始扫描"""
        if self.window_region is None:
            messagebox.showerror("错误", "请先定位游戏窗口")
            return

        resolution = self.res_var.get()
        if not resolution:
            messagebox.showerror("错误", "请先选择分辨率")
            return

        try:
            self.scanner = EquipmentScanner(
                resolution, self.res_cfg,
                on_log=self._log,
                on_progress=self._on_progress,
                on_cell_done=self._on_cell_done,
            )
        except Exception as e:
            messagebox.showerror("初始化失败", str(e))
            return

        left, top, w, h = self.window_region
        window_title = getattr(self, '_window_title', None)
        self.scanner.start_scan(left, top, w, h, window_title)

        self.btn_scan.configure(state=tk.DISABLED)
        self.btn_resume.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.NORMAL)
        self.btn_merge.configure(state=tk.DISABLED)
        self.status_var.set("扫描中...")

    def _resume_scan(self):
        """继续扫描下一页"""
        if self.scanner is None:
            return
        self.scanner.resume()
        self.btn_resume.configure(state=tk.DISABLED)
        self.status_var.set("扫描中...")

    def _stop_scan(self):
        """停止扫描"""
        if self.scanner:
            self.scanner.stop()

        self.btn_scan.configure(state=tk.NORMAL)
        self.btn_resume.configure(state=tk.DISABLED)
        self.btn_stop.configure(state=tk.DISABLED)

        if self.scan_results:
            self.btn_merge.configure(state=tk.NORMAL)

        self.status_var.set("扫描已停止")
        self._log("⏹ 扫描已停止")

    def _on_progress(self, current, total):
        """进度回调（在子线程中调用）"""
        self.root.after(0, lambda: self.progress.configure(maximum=total, value=current))

    def _on_cell_done(self, idx, name, stats):
        """单格扫描完成回调（在子线程中调用）"""
        self.scan_results.append({"name": name, "a": stats[0], "b": stats[1], "c": stats[2]})
        self.root.after(0, lambda: self._add_result_row(name, stats[0], stats[1], stats[2]))

        # 检测到页面扫描完成 → 更新按钮状态
        if self.scanner and self.scanner.paused:
            self.root.after(0, self._on_page_complete)

    def _on_page_complete(self):
        """页面扫描完成后更新 UI"""
        self.btn_resume.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.NORMAL)
        self.status_var.set("⏸️ 页面完成 — 请翻页后点击「继续下一页」或「停止」")

    def _add_result_row(self, name, a, b, c):
        """添加结果行"""
        self.result_tree.insert("", tk.END, values=(name, a, b, c))
        self.lbl_total.configure(text=f"共 {len(self.result_tree.get_children())} 件装备")
        if self.result_tree.get_children():
            self.btn_merge.configure(state=tk.NORMAL)

    def _clear_results(self):
        """清空结果"""
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        self.scan_results.clear()
        self.lbl_total.configure(text="共 0 件装备")
        self.btn_merge.configure(state=tk.DISABLED)
        self._log("🗑 结果已清空")

    def _delete_selected(self):
        """删除选中的结果行"""
        selected = self.result_tree.selection()
        for item in selected:
            values = self.result_tree.item(item, "values")
            self.result_tree.delete(item)
            # 同步删除 scan_results
            self.scan_results = [r for r in self.scan_results
                                 if not (r["name"] == values[0]
                                         and r["a"] == int(values[1])
                                         and r["b"] == int(values[2])
                                         and r["c"] == int(values[3]))]
        self.lbl_total.configure(text=f"共 {len(self.result_tree.get_children())} 件装备")

    def _on_tree_double_click(self, event):
        """双击单元格进入编辑模式"""
        region = self.result_tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self.result_tree.identify_column(event.x)
        item = self.result_tree.identify_row(event.y)
        if not item:
            return
        col_idx = int(col[1:]) - 1
        col_names = ("名称", "a", "b", "c")

        bbox = self.result_tree.bbox(item, col)
        if not bbox:
            return

        edit = tk.Entry(self.result_tree)
        edit.place(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3])
        edit.insert(0, self.result_tree.set(item, col_names[col_idx]))
        edit.select_range(0, tk.END)
        edit.focus_set()

        def save_edit():
            new_val = edit.get().strip()
            if new_val:
                values = list(self.result_tree.item(item, "values"))
                values[col_idx] = new_val
                self.result_tree.item(item, values=values)
                self._sync_tree_to_results()
            edit.destroy()

        edit.bind("<Return>", lambda e: save_edit())
        edit.bind("<FocusOut>", lambda e: save_edit())

    def _sync_tree_to_results(self):
        """将表格数据同步回 scan_results"""
        self.scan_results = []
        for item in self.result_tree.get_children():
            v = self.result_tree.item(item, "values")
            self.scan_results.append({
                "name": v[0], "a": int(v[1]), "b": int(v[2]), "c": int(v[3])
            })

    # ── 预览 & 导出 ──────────────────────────────────────

    def _preview_attr(self):
        """预览属性识别区域（调试用）"""
        if self.window_region is None:
            messagebox.showerror("错误", "请先定位游戏窗口")
            return

        resolution = self.res_var.get()
        cfg = self.res_cfg.get(resolution)
        if cfg is None:
            messagebox.showerror("错误", f"未找到分辨率配置: {resolution}")
            return

        cap = WindowCapture()
        left, top, w, h = self.window_region
        cap.set_region(left, top, w, h)
        frame = cap.capture()

        recognizer = AttributeRecognizer(cfg["detail"], cfg["color"])
        viz = recognizer.debug_visualize(frame, num_attrs=3)
        stats = recognizer.recognize(frame, num_attrs=3)

        self._log(f"🔍 属性预览: a={stats[0]} b={stats[1]} c={stats[2]}")

        # 用 OpenCV 显示：左侧属性裁剪区 + 右侧带标注的完整画面
        attr_x, attr_y = cfg["detail"]["attr_x"], cfg["detail"]["attr_y"]
        attr_w, attr_h = cfg["detail"]["attr_w"], cfg["detail"]["attr_h"]
        attr_roi = frame[attr_y:attr_y+attr_h, attr_x:attr_x+attr_w]

        # 拼接：左边属性ROI，右边标注图
        viz_resized = cv2.resize(viz, (attr_roi.shape[1], attr_roi.shape[0]))
        combined = np.hstack([attr_roi, viz_resized])
        cv2.imshow("Attr Preview - Left:ROI  Right:Full (press any key)", combined)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    def _export_temp(self):
        """导出临时配置文件"""
        if not self.scan_results:
            messagebox.showinfo("提示", "没有可导出的结果")
            return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json")],
            initialdir=_app_dir(),
            initialfile="temp_config.json",
        )
        if not filepath:
            return

        self.merger.save_temp(self.scan_results, filepath)
        self._log(f"💾 临时配置已导出: {filepath}")
        messagebox.showinfo("导出成功", f"临时配置已保存到:\n{filepath}")

    def _confirm_merge(self):
        """确认合并到 setConfig.json"""
        if not self.scan_results:
            messagebox.showinfo("提示", "没有可合并的结果")
            return

        # 生成预览
        temp = self.merger.generate_temp_config(self.scan_results)
        eq_count = len(temp.get("equipment", {}))

        answer = messagebox.askyesno(
            "确认合并",
            f"即将把 {eq_count} 种装备（共 {len(self.scan_results)} 条记录）合并到 setConfig.json。\n\n"
            f"已存在的装备记录将被覆盖，角色和小队配置不受影响。\n\n确认继续？"
        )
        if not answer:
            return

        try:
            self.merger.merge(self.scan_results, dry_run=False)
            self._log(f"✅ 成功合并 {eq_count} 种装备到 setConfig.json")
            messagebox.showinfo("合并成功",
                                f"已更新 setConfig.json！\n"
                                f"新增/更新 {eq_count} 种装备。\n\n"
                                f"提示：可在 ZMDset 主程序中点击「🔄 刷新」查看最新数据。")
        except Exception as e:
            messagebox.showerror("合并失败", str(e))

    def _on_close(self):
        """窗口关闭"""
        if self.scanner:
            self.scanner.stop()
        self._unregister_app_hotkeys()
        cv2.destroyAllWindows()
        self.root.destroy()

    # ── 应用程序全局快捷键 ───────────────────────────

    def _register_app_hotkeys(self):
        """注册 F5=开始扫描, F6=继续, F7=停止"""
        if not HAS_KEYBOARD:
            return
        try:
            keyboard.add_hotkey("f5", self._hotkey_start_scan, suppress=False)
            keyboard.add_hotkey("f6", self._hotkey_resume, suppress=False)
            keyboard.add_hotkey("f7", self._hotkey_stop, suppress=False)
            self._log("⌨️  快捷键已注册: F5=开始扫描  F6=继续下一页  F7=停止")
        except Exception:
            pass

    def _unregister_app_hotkeys(self):
        """清理全局快捷键"""
        if not HAS_KEYBOARD:
            return
        for key in ["f5", "f6", "f7"]:
            try:
                keyboard.remove_hotkey(key)
            except Exception:
                pass

    def _hotkey_start_scan(self):
        """F5: 开始扫描"""
        self.root.after(0, self._start_scan)

    def _hotkey_resume(self):
        """F6: 继续下一页"""
        if self.scanner and self.scanner.paused:
            self.root.after(0, self._resume_scan)

    def _hotkey_stop(self):
        """F7: 停止扫描"""
        self.root.after(0, self._stop_scan)


# ============================================================
#  命令行入口（开发中）
# ============================================================

def cli_mode():
    """命令行模式"""
    print("ZMDset 装备识别工具 - CLI 模式（开发中）")
    print("请使用 GUI 模式: python getconfig.py")
    sys.exit(0)


# ============================================================
#  主入口
# ============================================================

def main():
    if "--cli" in sys.argv:
        cli_mode()

    # 检查依赖
    missing = []
    if not HAS_MSS:
        missing.append("mss")
    if not HAS_PYAUTOGUI:
        missing.append("pyautogui")
    if not HAS_TESSERACT:
        print("⚠️ 警告: 未安装 pytesseract，装备名称 OCR 功能将不可用")
        print("   安装: pip install pytesseract")
        print("   还需安装 Tesseract-OCR 引擎: https://github.com/UB-Mannheim/tesseract/wiki")
    elif TESSERACT_PATH is None:
        print("⚠️ 警告: 未找到 Tesseract-OCR 引擎，装备名称 OCR 功能将不可用")
        print("   下载安装: https://github.com/UB-Mannheim/tesseract/wiki")
        print("   安装时请勾选中文简体语言包 (Chinese Simplified)")
    else:
        print(f"✅ Tesseract-OCR 已就绪: {TESSERACT_PATH}")

    if missing:
        msg = f"缺少必要的依赖: {', '.join(missing)}\n\n请运行:\npip install {' '.join(missing)}\n\n然后重新启动程序。"
        # 在 GUI 模式下用 messagebox，但先尝试导入 tkinter
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("依赖缺失", msg)
            root.destroy()
        except Exception:
            print(f"错误: {msg}")
        sys.exit(1)

    app = ScannerApp()


if __name__ == "__main__":
    main()
