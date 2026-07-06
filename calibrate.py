#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZMDset — 区域标定辅助工具 (calibrate)

功能：
  1. 通过窗口标题定位游戏窗口
  2. 实时显示鼠标的屏幕绝对坐标和相对于窗口的坐标
  3. 输入相对坐标范围（左、上、右、下），预览截图区域
  4. 方便你标定 resolution_config.json 中的网格区域、属性区域等坐标

用法：
  python calibrate.py

依赖：与 getconfig.py 相同
"""

import json
import os
import sys
import time
from difflib import SequenceMatcher
import tkinter as tk
from tkinter import ttk, messagebox

import cv2
import numpy as np

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

# ── Tesseract 路径检测 ─────────────────────────────────
def _find_tesseract():
    import subprocess
    common_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p
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
OCR_AVAILABLE = HAS_TESSERACT and TESSERACT_PATH is not None
if OCR_AVAILABLE:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


class CalibratorApp:
    """区域标定工具"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ZMDset — 区域标定工具")
        self.root.minsize(620, 600)

        self.window_title = None
        self.window_region = None  # (left, top, width, height)
        self._tracking = False  # 鼠标追踪开关
        self._sct = mss.MSS() if HAS_MSS else None
        self._hotkeys = []  # 全局快捷键 ID 列表

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    # ── UI ─────────────────────────────────────────────

    def _build_ui(self):
        # 顶部：窗口定位
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="窗口标题关键词:").pack(side=tk.LEFT)
        self.entry_title = ttk.Entry(top, width=18)
        self.entry_title.pack(side=tk.LEFT, padx=5)
        self.entry_title.bind("<Return>", lambda e: self._search_and_locate())
        ttk.Button(top, text="🔍 定位窗口", command=self._search_and_locate).pack(side=tk.LEFT, padx=2)
        self.btn_list = ttk.Button(top, text="🔄 列出全部", command=self._toggle_list)
        self.btn_list.pack(side=tk.LEFT, padx=2)
        self.lbl_win_status = ttk.Label(top, text="未定位", foreground="red")
        self.lbl_win_status.pack(side=tk.LEFT, padx=15)

        # ── 窗口列表（内联，默认隐藏）──
        self._list_frame = ttk.LabelFrame(self.root, text="窗口列表（双击选择）", padding=5)
        list_top = ttk.Frame(self._list_frame)
        list_top.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(list_top, text="过滤:").pack(side=tk.LEFT)
        self.entry_filter = ttk.Entry(list_top, width=20)
        self.entry_filter.pack(side=tk.LEFT, padx=4)
        self.entry_filter.bind("<KeyRelease>", lambda e: self._refresh_list())
        ttk.Button(list_top, text="🔄 刷新", command=self._refresh_list, width=6).pack(side=tk.LEFT, padx=2)
        self.lbl_list_count = ttk.Label(list_top, text="", foreground="#888")
        self.lbl_list_count.pack(side=tk.LEFT, padx=8)

        lb_frame = ttk.Frame(self._list_frame)
        lb_frame.pack(fill=tk.BOTH, expand=True)
        self._win_listbox = tk.Listbox(lb_frame, font=("Consolas", 9), height=8)
        self._win_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_scroll = ttk.Scrollbar(lb_frame, orient=tk.VERTICAL, command=self._win_listbox.yview)
        self._win_listbox.configure(yscrollcommand=list_scroll.set)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._win_listbox.bind("<Double-Button-1>", lambda e: self._on_list_select())

        btn_frame = ttk.Frame(self._list_frame)
        btn_frame.pack(fill=tk.X, pady=(3, 0))
        ttk.Button(btn_frame, text="确认选中", command=self._on_list_select).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="隐藏列表 ▲", command=self._hide_list).pack(side=tk.RIGHT)

        self._all_windows = []  # 缓存窗口列表
        self._list_visible = False

        # ── 中部：鼠标坐标 ──
        self._coord_frame = ttk.LabelFrame(self.root, text="鼠标位置", padding=10)
        self._coord_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        row1 = ttk.Frame(self._coord_frame)
        row1.pack(fill=tk.X)
        ttk.Label(row1, text="屏幕绝对坐标:", width=14).pack(side=tk.LEFT)
        self.lbl_abs = ttk.Label(row1, text="( — , — )", font=("Consolas", 11, "bold"), foreground="#333")
        self.lbl_abs.pack(side=tk.LEFT, padx=5)
        ttk.Label(row1, text="窗口相对坐标:", width=14).pack(side=tk.LEFT, padx=(20, 0))
        self.lbl_rel = ttk.Label(row1, text="( — , — )", font=("Consolas", 11, "bold"), foreground="#0066cc")
        self.lbl_rel.pack(side=tk.LEFT, padx=5)

        btn_row = ttk.Frame(self._coord_frame)
        btn_row.pack(fill=tk.X, pady=(8, 0))
        self.btn_track = ttk.Button(btn_row, text="▶ 开始追踪鼠标", command=self._toggle_tracking)
        self.btn_track.pack(side=tk.LEFT)
        ttk.Button(btn_row, text="📋 复制当前相对坐标", command=self._copy_rel_pos).pack(side=tk.LEFT, padx=5)
        ttk.Label(btn_row, text="💡 追踪时移动鼠标即可实时查看坐标",
                  foreground="#888888", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT, padx=15)

        # ── 区域预览 ──
        roi_frame = ttk.LabelFrame(self.root, text="区域预览（窗口内相对坐标）", padding=10)
        roi_frame.pack(fill=tk.X, padx=10, pady=(5, 5))

        input_row = ttk.Frame(roi_frame)
        input_row.pack(fill=tk.X)
        ttk.Label(input_row, text="左:").pack(side=tk.LEFT)
        self.entry_l = ttk.Entry(input_row, width=6)
        self.entry_l.pack(side=tk.LEFT, padx=(2, 8))
        ttk.Label(input_row, text="上:").pack(side=tk.LEFT)
        self.entry_t = ttk.Entry(input_row, width=6)
        self.entry_t.pack(side=tk.LEFT, padx=(2, 8))
        ttk.Label(input_row, text="右:").pack(side=tk.LEFT)
        self.entry_r = ttk.Entry(input_row, width=6)
        self.entry_r.pack(side=tk.LEFT, padx=(2, 8))
        ttk.Label(input_row, text="下:").pack(side=tk.LEFT)
        self.entry_b = ttk.Entry(input_row, width=6)
        self.entry_b.pack(side=tk.LEFT, padx=(2, 8))
        ttk.Button(input_row, text="👁 预览区域",
                   command=self._preview_roi).pack(side=tk.LEFT, padx=5)
        ttk.Button(input_row, text="🔲 网格检测",
                   command=self._grid_detect_roi).pack(side=tk.LEFT, padx=2)
        ttk.Button(input_row, text="📊 属性检测",
                   command=self._attr_detect_roi).pack(side=tk.LEFT, padx=2)
        if OCR_AVAILABLE:
            ttk.Button(input_row, text="🔤 OCR识别",
                       command=self._ocr_roi).pack(side=tk.LEFT, padx=2)
        ttk.Button(input_row, text="📋 导出为JSON",
                   command=self._export_roi).pack(side=tk.LEFT, padx=2)

        # 快捷填入按钮
        fill_row = ttk.Frame(roi_frame)
        fill_row.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(fill_row, text="快捷填入当前鼠标位置:", foreground="#666").pack(side=tk.LEFT)
        ttk.Button(fill_row, text="设为左上", command=self._set_left_top).pack(side=tk.LEFT, padx=3)
        ttk.Button(fill_row, text="设为右下", command=self._set_right_bottom).pack(side=tk.LEFT, padx=3)
        if HAS_KEYBOARD:
            ttk.Label(fill_row, text="  快捷键: F1=左上  F2=右下",
                      foreground="#888888", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT, padx=5)

    # ── 窗口定位 ──────────────────────────────────────

    def _search_and_locate(self):
        if not HAS_PYGETWINDOW:
            messagebox.showerror("错误", "pygetwindow 未安装")
            return
        keyword = self.entry_title.get().strip()
        if not keyword:
            self._show_list()
            return
        windows = gw.getWindowsWithTitle(keyword)
        if not windows:
            messagebox.showinfo("未找到", f"未找到标题包含「{keyword}」的窗口")
            return
        self._use_window(windows[0])

    def _load_all_windows(self):
        """加载所有窗口列表（缓存）"""
        if not HAS_PYGETWINDOW:
            self._all_windows = []
            return
        self._all_windows = [w for w in gw.getAllWindows()
                             if w.title.strip() and w.width > 100 and w.height > 100]
        self._all_windows.sort(key=lambda w: w.title)

    def _toggle_list(self):
        """切换窗口列表显示/隐藏"""
        if self._list_visible:
            self._hide_list()
        else:
            self._show_list()

    def _show_list(self):
        """显示内联窗口列表"""
        if not HAS_PYGETWINDOW:
            return
        self._load_all_windows()
        self._list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5),
                              before=self._coord_frame)
        self._list_visible = True
        self.btn_list.configure(text="🔄 隐藏列表")
        self._refresh_list()

    def _hide_list(self):
        """隐藏内联窗口列表"""
        self._list_frame.pack_forget()
        self._list_visible = False
        self.btn_list.configure(text="🔄 列出全部")

    def _refresh_list(self):
        """刷新列表显示（支持过滤）"""
        self._load_all_windows()
        self._win_listbox.delete(0, tk.END)
        keyword = self.entry_filter.get().strip().lower()
        count = 0
        for w in self._all_windows:
            if keyword and keyword not in w.title.lower():
                continue
            title = w.title[:70]
            self._win_listbox.insert(tk.END, f"{title:<72} {w.width}×{w.height}  @({w.left},{w.top})")
            count += 1
        self.lbl_list_count.configure(text=f"{count}/{len(self._all_windows)} 个窗口")

    def _on_list_select(self):
        """用户双击或点击确认后选中窗口"""
        sel = self._win_listbox.curselection()
        if not sel:
            return
        keyword = self.entry_filter.get().strip().lower()
        matched = [w for w in self._all_windows
                   if not keyword or keyword in w.title.lower()]
        if sel[0] < len(matched):
            self._use_window(matched[sel[0]])
            self._hide_list()

    def _use_window(self, w):
        if w.isMinimized:
            try:
                w.restore()
                time.sleep(0.3)
            except Exception:
                pass
        self.window_title = w.title
        self.window_region = (w.left, w.top, w.width, w.height)
        self.lbl_win_status.configure(
            text=f"已定位: 「{w.title[:30]}」 {w.width}×{w.height} @({w.left},{w.top})",
            foreground="green")
        # 自动开始追踪
        if not self._tracking:
            self._toggle_tracking()
        # 注册全局快捷键 F1/F2
        self._register_hotkeys()

    # ── 鼠标追踪 ──────────────────────────────────────

    def _toggle_tracking(self):
        if not HAS_PYAUTOGUI:
            messagebox.showerror("错误", "pyautogui 未安装")
            return
        self._tracking = not self._tracking
        if self._tracking:
            self.btn_track.configure(text="⏸ 停止追踪")
            self._track_loop()
        else:
            self.btn_track.configure(text="▶ 开始追踪鼠标")

    def _track_loop(self):
        if not self._tracking:
            return
        try:
            ax, ay = pyautogui.position()
            self.lbl_abs.configure(text=f"({ax}, {ay})")
            if self.window_region:
                wx, wy = ax - self.window_region[0], ay - self.window_region[1]
                ww, wh = self.window_region[2], self.window_region[3]
                if 0 <= wx < ww and 0 <= wy < wh:
                    self.lbl_rel.configure(text=f"({wx}, {wy})", foreground="#0066cc")
                else:
                    self.lbl_rel.configure(text=f"({wx}, {wy})  ←窗口外", foreground="#cc6600")
            else:
                self.lbl_rel.configure(text="( — , — )")
        except Exception:
            pass
        self.root.after(80, self._track_loop)

    # ── 快捷填入 ──────────────────────────────────────

    def _get_rel_pos(self):
        """获取当前鼠标相对坐标，未定位返回 None"""
        if not HAS_PYAUTOGUI or not self.window_region:
            return None
        ax, ay = pyautogui.position()
        return (ax - self.window_region[0], ay - self.window_region[1])

    def _set_left_top(self):
        pos = self._get_rel_pos()
        if pos is None:
            messagebox.showinfo("提示", "请先定位窗口")
            return
        self.entry_l.delete(0, tk.END)
        self.entry_l.insert(0, str(pos[0]))
        self.entry_t.delete(0, tk.END)
        self.entry_t.insert(0, str(pos[1]))

    def _set_right_bottom(self):
        pos = self._get_rel_pos()
        if pos is None:
            messagebox.showinfo("提示", "请先定位窗口")
            return
        self.entry_r.delete(0, tk.END)
        self.entry_r.insert(0, str(pos[0]))
        self.entry_b.delete(0, tk.END)
        self.entry_b.insert(0, str(pos[1]))

    def _copy_rel_pos(self):
        pos = self._get_rel_pos()
        if pos is None:
            messagebox.showinfo("提示", "请先定位窗口")
            return
        text = f"({pos[0]}, {pos[1]})"
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    # ── 区域预览 ──────────────────────────────────────

    def _get_roi_coords(self):
        """解析输入框，返回 (x, y, w, h) 或 None"""
        try:
            l = int(self.entry_l.get())
            t = int(self.entry_t.get())
            r = int(self.entry_r.get())
            b = int(self.entry_b.get())
            if l >= r or t >= b:
                messagebox.showerror("错误", "左 < 右 且 上 < 下")
                return None
            return (l, t, r - l, b - t)
        except ValueError:
            messagebox.showerror("错误", "请输入有效的整数坐标")
            return None

    def _preview_roi(self):
        if not self.window_region or not HAS_MSS:
            messagebox.showinfo("提示", "请先定位窗口")
            return
        roi = self._get_roi_coords()
        if roi is None:
            return

        x, y, w, h = roi
        win_l, win_t = self.window_region[0], self.window_region[1]

        monitor = {"left": win_l + x, "top": win_t + y, "width": w, "height": h}
        img = self._sct.grab(monitor)
        arr = np.array(img)
        frame = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)

        # 在图上标注范围和尺寸
        cv2.rectangle(frame, (0, 0), (w - 1, h - 1), (0, 255, 0), 2)
        cv2.putText(frame, f"({x},{y})  {w}x{h}", (5, h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

        window_name = f"ROI Preview: ({x},{y}) {w}x{h}  [press any key to close]"
        self._show_scaled(window_name, frame)
        cv2.destroyAllWindows()

    def _grid_detect_roi(self):
        """对选定区域复刻 getconfig 的网格检测，标注中心点"""
        if not self.window_region or not HAS_MSS:
            messagebox.showinfo("提示", "请先定位窗口")
            return
        roi = self._get_roi_coords()
        if roi is None:
            return

        x, y, w, h = roi
        win_l, win_t = self.window_region[0], self.window_region[1]

        monitor = {"left": win_l + x, "top": win_t + y, "width": w, "height": h}
        img = self._sct.grab(monitor)
        arr = np.array(img)
        frame = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # ── 与 getconfig 完全一致的网格检测流程 ──
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 31, 5)

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        binary = cv2.erode(binary, kernel, iterations=1)
        binary = cv2.dilate(binary, kernel, iterations=1)

        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # 用 ROI 区域的 w/h 反推 cell_size（假设 9 列）
        est_cell = w // 10  # 粗略估计
        tolerance = 0.35
        min_s = int(est_cell * (1 - tolerance))
        max_s = int(est_cell * (1 + tolerance))

        cells = []
        for cnt in contours:
            bx, by, bw, bh = cv2.boundingRect(cnt)
            if not (min_s < bw < max_s and min_s < bh < max_s):
                continue
            if not (0.7 < bw / bh < 1.4):
                continue
            if cv2.contourArea(cnt) / (bw * bh) < 0.5:
                continue
            cx, cy = bx + bw // 2, by + bh // 2
            cells.append((bx, by, bw, bh, cx, cy))

        # ── 排序 + 补全跳过的格子（与 getconfig 一致） ──
        if cells:
            # 按行分组
            sorted_by_y = sorted(cells, key=lambda c: c[5])
            rows, current = [], [sorted_by_y[0]]
            for cell in sorted_by_y[1:]:
                if abs(cell[5] - current[-1][5]) < est_cell * 0.5:
                    current.append(cell)
                else:
                    rows.append(sorted(current, key=lambda c: c[4]))
                    current = [cell]
            rows.append(sorted(current, key=lambda c: c[4]))

            # 逐行补全
            filled = []
            for row_idx, row_cells in enumerate(rows):
                pts = [(c[4], c[5]) for c in row_cells]
                if len(pts) <= 1:
                    filled.extend(pts)
                    continue
                gaps = [pts[i+1][0] - pts[i][0] for i in range(len(pts)-1)]
                avg_step = sum(gaps)/len(gaps) if gaps else est_cell
                row_filled = [pts[0]]
                for i in range(len(pts)-1):
                    x1, y1 = pts[i]
                    x2, y2 = pts[i+1]
                    dist = x2 - x1
                    while dist > avg_step * 1.4:
                        x1 += avg_step
                        row_filled.append((int(x1), int((y1+y2)/2)))
                        dist = x2 - x1
                    row_filled.append(pts[i+1])
                filled.extend(row_filled)
            cells = filled  # [(cx, cy), ...]

        # ── 在原图上标注 ──
        viz = frame.copy()
        cv2.rectangle(viz, (0, 0), (w - 1, h - 1), (0, 255, 0), 2)

        detected_count = 0
        for i, item in enumerate(cells):
            if len(item) == 6:
                bx, by, bw, bh, cx, cy = item
                cv2.rectangle(viz, (bx, by), (bx+bw, by+bh), (255, 0, 0), 1)
                detected_count += 1
            else:
                cx, cy = item
                # 补全的格子用虚线框标注（虚线太复杂用淡色框代替）
                half = est_cell // 2
                cv2.rectangle(viz, (cx-half, cy-half), (cx+half, cy+half),
                              (200, 200, 200), 1)
            cv2.circle(viz, (cx, cy), 3, (0, 0, 255), -1)
            wx, wy = x + cx, y + cy
            cv2.putText(viz, f"({wx},{wy})", (cx + 5, cy - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1)

        title = f"Grid: {detected_count} detected + {len(cells)-detected_count} filled = {len(cells)} total  [press any key]"
        self._show_scaled(title, viz)
        cv2.destroyAllWindows()

    def _show_scaled(self, title, img, min_size=200):
        """自动放大过小的图像后显示"""
        h, w = img.shape[:2]
        if w < min_size or h < min_size:
            scale = max(min_size / w, min_size / h)
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        cv2.imshow(title, img)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    def _attr_detect_roi(self):
        """对选定区域执行属性检测（与 getconfig 一致：白像素判定 2/3 属性 + 分划）"""
        if not self.window_region or not HAS_MSS:
            messagebox.showinfo("提示", "请先定位窗口")
            return
        roi = self._get_roi_coords()
        if roi is None:
            return

        x, y, w, h = roi
        win_l, win_t = self.window_region[0], self.window_region[1]

        monitor = {"left": win_l + x, "top": win_t + y, "width": w, "height": h}
        img = self._sct.grab(monitor)
        arr = np.array(img)
        frame = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        roi_h, roi_w = frame.shape[:2]

        # 底部 15px 白像素判定属性数量
        strip_h = min(15, roi_h)
        bottom = frame[-strip_h:, :, :]
        white = cv2.inRange(bottom, (200, 200, 200), (255, 255, 255))
        ratio = np.sum(white) / 255 / (strip_h * roi_w)
        num_attrs = 2 if ratio > 0.10 else 3

        # 分划方案（像素区间）
        layout = {"2": [[0, 15], [45, 60]], "3": [[0, 15], [25, 40], [70, 85]]}
        scheme = layout[str(num_attrs)]

        # 可视化
        viz = frame.copy()
        cv2.rectangle(viz, (0, 0), (w - 1, h - 1), (0, 255, 0), 2)

        for i in range(num_attrs):
            y1, y2 = scheme[i]
            y1 = max(0, min(y1, roi_h))
            y2 = max(y1 + 1, min(y2, roi_h))
            cv2.rectangle(viz, (0, y1), (roi_w - 1, y2), (0, 255, 255), 1)
            cv2.putText(viz, f"attr{i+1} ({y1}-{y2})", (5, (y1 + y2) // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)

        cv2.putText(viz, f"Mode: {num_attrs}attrs  white:{ratio:.1%}",
                    (5, roi_h - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 255), 1)

        self._show_scaled(f"Attr Detect: {num_attrs} attrs (white={ratio:.0%})", viz)

    def _export_roi(self):
        """导出区域坐标为 JSON 片段"""
        roi = self._get_roi_coords()
        if roi is None:
            return
        x, y, w, h = roi
        snippet = {
            "attr_x": x, "attr_y": y,
            "attr_w": w, "attr_h": h,
        }
        text = json.dumps(snippet, ensure_ascii=False, indent=4)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        messagebox.showinfo("已复制", f"JSON 片段已复制到剪贴板:\n\n{text}")

    def _load_equipment_names(self):
        """从 setConfig.json 加载所有已有装备名"""
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setConfig.json")
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return list(data.get("equipment", {}).keys())
        except Exception:
            return []

    def _best_match(self, raw_name, candidates):
        """在候选装备名中找最相似的，相似度 >= 0.6 才采纳"""
        if not raw_name or not candidates:
            return None
        best, best_score = None, 0
        for c in candidates:
            score = SequenceMatcher(None, raw_name, c).ratio()
            if score > best_score:
                best_score = score
                best = c
        return best if best_score >= 0.7 else None

    def _ocr_roi(self):
        """对选定区域执行 OCR 识别（与 getconfig.py 完全一致）"""
        if not self.window_region or not HAS_MSS:
            messagebox.showinfo("提示", "请先定位窗口")
            return
        roi = self._get_roi_coords()
        if roi is None:
            return

        x, y, w, h = roi
        win_l, win_t = self.window_region[0], self.window_region[1]

        # 截图
        monitor = {"left": win_l + x, "top": win_t + y, "width": w, "height": h}
        img = self._sct.grab(monitor)
        arr = np.array(img)
        frame = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)

        # ── 预处理 ──
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

        # 暗底白字 → 反色为白底黑字（Tesseract 最佳输入）
        gray = cv2.bitwise_not(gray)
        # 左侧 2/3 区域计算 OTSU 阈值，全图应用（右侧可能有 UI 干扰）
        h_g, w_g = gray.shape
        left_w = w_g * 2 // 3
        thresh_val, _ = cv2.threshold(
            gray[:, :left_w], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        _, binary = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY)

        # OCR
        _BLACKLIST = "01.,;:!?[]'\"`_|·"
        try:
            text = pytesseract.image_to_string(
                binary, lang="chi_sim",
                config=f"--psm 7 -c tessedit_char_blacklist={_BLACKLIST}"
            )
            raw = text.strip().replace(" ", "").replace("\n", "").replace("\r", "")
        except Exception as e:
            raw = ""

        # 模糊匹配已有装备名
        candidates = self._load_equipment_names()
        matched = self._best_match(raw, candidates)
        display = matched if matched else raw

        # 显示：左侧二值图 + 右侧原图 + 识别结果
        frame_big = cv2.resize(frame, (binary.shape[1], binary.shape[0]),
                               interpolation=cv2.INTER_CUBIC)
        binary_bgr = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
        combined = np.hstack([binary_bgr, frame_big])
        cv2.putText(combined, f"OCR: {raw if raw else '(空)'}",
                    (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        if matched:
            score = SequenceMatcher(None, raw, matched).ratio()
            cv2.putText(combined, f"Match: {matched} ({score:.0%})",
                        (5, combined.shape[0] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
            result = matched
        else:
            cv2.putText(combined, "Match: (none)",
                        (5, combined.shape[0] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
            result = raw

        window_name = f"OCR: {display if display else '(empty)'}  [press any key]"
        self._show_scaled(window_name, combined)
        cv2.destroyAllWindows()

        # 也输出到剪贴板
        if result:
            self.root.clipboard_clear()
            self.root.clipboard_append(result)
            self.root.update()

    def _on_close(self):
        self._tracking = False
        self._unregister_hotkeys()
        cv2.destroyAllWindows()
        self.root.destroy()

    # ── 全局快捷键 ───────────────────────────────────

    def _register_hotkeys(self):
        """注册 F1=设为左上, F2=设为右下"""
        if not HAS_KEYBOARD:
            return
        self._unregister_hotkeys()
        try:
            self._hotkeys.append(
                keyboard.add_hotkey("f1", self._set_left_top, suppress=False))
            self._hotkeys.append(
                keyboard.add_hotkey("f2", self._set_right_bottom, suppress=False))
        except Exception:
            pass

    def _unregister_hotkeys(self):
        """清理所有全局快捷键"""
        if not HAS_KEYBOARD:
            return
        for hk in self._hotkeys:
            try:
                keyboard.remove_hotkey(hk)
            except Exception:
                pass
        self._hotkeys.clear()


def main():
    missing = []
    if not HAS_MSS:
        missing.append("mss")
    if not HAS_PYAUTOGUI:
        missing.append("pyautogui")
    if not HAS_PYGETWINDOW:
        missing.append("pygetwindow")

    if missing:
        msg = f"缺少依赖: {', '.join(missing)}\n\npip install {' '.join(missing)}"
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("依赖缺失", msg)
            root.destroy()
        except Exception:
            print(msg)
        sys.exit(1)

    CalibratorApp()


if __name__ == "__main__":
    main()
