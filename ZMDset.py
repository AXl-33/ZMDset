# -*- coding: utf-8 -*-
"""
ZMDset - 小队装备管理工具

功能：
  - 左侧勾选可能上场的小队方案
  - 右侧展示选中队伍4人的详细装备
  - 底部汇总所有方案所需装备的总数
  - 同一小队内重复装备会按实际数量统计
  - 不同小队只取各装备需求的最大值（同一时间仅一队上场）

配置文件格式（setConfig.json）—— JSON:
  {
    "characters": {
      "角色名": [{ "set": "套装名", "armor": "护甲", "gauntlet": "护手",
                    "accessory1": "配件1", "accessory2": "配件2" }, ...],
      ...
    },
    "teams": {
      "小队名": [{ "character": "角色名", "set": "套装名" }, ...  (4名成员) ],
      ...
    }
  }
"""

import json
import os
import sys
import tkinter as tk
from tkinter import ttk
from collections import Counter, defaultdict


def _app_dir():
    """返回应用程序所在目录（兼容 PyInstaller 打包）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# ============================================================
#  配置解析模块
# ============================================================

class CharacterGear:
    """单个角色的一套装备"""
    __slots__ = ('char_name', 'set_name', 'armor', 'gauntlet', 'acc1', 'acc2')

    def __init__(self, char_name, set_name, armor, gauntlet, acc1, acc2):
        self.char_name = char_name
        self.set_name = set_name
        self.armor = armor
        self.gauntlet = gauntlet
        self.acc1 = acc1
        self.acc2 = acc2

    @property
    def all_gear(self):
        """返回该角色的所有装备列表"""
        return [self.armor, self.gauntlet, self.acc1, self.acc2]

    @property
    def key(self):
        return (self.char_name, self.set_name)


class TeamComposition:
    """一支小队由4个成员组成"""
    __slots__ = ('name', 'members', 'comment')

    def __init__(self, name, members, comment=""):
        self.name = name
        self.members = members
        self.comment = comment

    def count_gear(self, gear_dict):
        """
        统计该小队的装备需求（考虑队内重复）
        gear_dict: {(char_name, set_name): CharacterGear}
        返回 Counter {装备名: 数量}
        """
        counts = Counter()
        for member_key in self.members:
            if member_key in gear_dict:
                for g in gear_dict[member_key].all_gear:
                    counts[g] += 1
        return counts


class ConfigLoader:
    """加载并解析 set.config 文件"""

    def __init__(self, filepath="set.config"):
        self.filepath = filepath
        self.char_gear = {}       # {(char_name, set_name): CharacterGear}
        self.char_sets = defaultdict(list)  # {char_name: [set_name, ...]}
        self.teams = {}           # {team_name: TeamComposition}
        self.owned_equipment = {} # {装备名: [(a, b, c), ...]}
        self._parse()

    def _parse(self):
        if not os.path.exists(self.filepath):
            raise FileNotFoundError(f"配置文件不存在: {self.filepath}")

        with open(self.filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 解析角色装备
        for char_name, sets in data.get("characters", {}).items():
            for gear_data in sets:
                set_name = gear_data.get("set", "默认")
                gear = CharacterGear(
                    char_name,
                    set_name,
                    gear_data.get("armor", ""),
                    gear_data.get("gauntlet", ""),
                    gear_data.get("accessory1", ""),
                    gear_data.get("accessory2", ""),
                )
                self.char_gear[gear.key] = gear
                self.char_sets[char_name].append(set_name)

        # 解析小队（支持新旧两种格式）
        for team_name, team_data in data.get("teams", {}).items():
            comment = ""
            if isinstance(team_data, dict):
                # 新格式: {"members": [...], "comment": "..."}
                members_raw = team_data.get("members", [])
                comment = team_data.get("comment", "")
            elif isinstance(team_data, list):
                # 旧格式: [{character, set}, ...]
                members_raw = team_data
            else:
                continue

            member_list = []
            for m in members_raw:
                if "comment" in m:
                    # 数组内的 comment 占位项
                    continue
                char_name = m.get("character", "")
                set_name = m.get("set", "默认")
                member_list.append((char_name, set_name))
            self.teams[team_name] = TeamComposition(team_name, member_list, comment)

        # 解析已有装备（兼容单对象和数组两种格式）
        for gear_name, stats_val in data.get("equipment", {}).items():
            if isinstance(stats_val, list):
                # 数组格式: [{"a":6,"b":5,"c":3}, ...]
                items = []
                for s in stats_val:
                    items.append((s.get("a", 0), s.get("b", 0), s.get("c", 0)))
                self.owned_equipment[gear_name] = items
            elif isinstance(stats_val, dict):
                # 单对象格式: {"a":6,"b":5,"c":3}
                self.owned_equipment[gear_name] = [
                    (stats_val.get("a", 0), stats_val.get("b", 0), stats_val.get("c", 0))
                ]
            else:
                self.owned_equipment[gear_name] = []

    def get_best_stats(self, gear_name):
        """获取某装备的最佳属性值字符串（取 a+b+c 总和最大的），如 '653'；没有则返回 None"""
        items = self.owned_equipment.get(gear_name)
        if not items:
            return None
        best = max(items, key=lambda t: t[0] + t[1] + t[2])
        return f"{best[0]}{best[1]}{best[2]}"

    def get_stats_display(self, gear_name):
        """获取推荐属性展示文本：有→'653'，无→'暂缺'"""
        s = self.get_best_stats(gear_name)
        return s if s else "暂缺"

    def count_owned(self, gear_name):
        """检查拥有某装备的数量"""
        items = self.owned_equipment.get(gear_name)
        return len(items) if items else 0

    def get_team_gear_list(self, team_name):
        """获取小队每个成员的装备详情，用于右侧展示"""
        team = self.teams.get(team_name)
        if not team:
            return []
        result = []
        for idx, (char_name, set_name) in enumerate(team.members, 1):
            key = (char_name, set_name)
            gear = self.char_gear.get(key)
            if gear:
                result.append({
                    "序号": idx,
                    "角色": char_name,
                    "套装": set_name,
                    "护甲": gear.armor,
                    "护手": gear.gauntlet,
                    "配件1": gear.acc1,
                    "配件2": gear.acc2,
                })
            else:
                result.append({
                    "序号": idx,
                    "角色": char_name,
                    "套装": f"{set_name}(未定义)",
                    "护甲": "—",
                    "护手": "—",
                    "配件1": "—",
                    "配件2": "—",
                })
        return result

    def allocate_gear_for_team(self, team_name):
        """
        为小队分配已有装备（考虑同装备多人争用，按属性和从大到小依次分配）。
        返回每个成员的装备名 + 分配到的属性值。
        """
        team = self.teams.get(team_name)
        if not team:
            return []

        # Step 1: 收集全队装备需求 {装备名: [(成员索引, 槽位名), ...]}
        gear_demands = defaultdict(list)
        members_raw = []
        for idx, (char_name, set_name) in enumerate(team.members):
            key = (char_name, set_name)
            gear = self.char_gear.get(key)
            if gear:
                slots = [("护甲", gear.armor), ("护手", gear.gauntlet),
                         ("配件1", gear.acc1), ("配件2", gear.acc2)]
                members_raw.append((idx, char_name, set_name, slots))
                for slot, gname in slots:
                    gear_demands[gname].append((idx, slot))
            else:
                members_raw.append((idx, char_name, set_name, []))

        # Step 2: 每种装备按属性和降序分配
        allocation = {}  # {(成员索引, 槽位): "333" 或 "暂缺"}
        for gname, demands in gear_demands.items():
            items = sorted(self.owned_equipment.get(gname, []),
                           key=lambda t: t[0] + t[1] + t[2], reverse=True)
            for di, (char_idx, slot) in enumerate(demands):
                if di < len(items):
                    it = items[di]
                    allocation[(char_idx, slot)] = f"{it[0]}{it[1]}{it[2]}"
                else:
                    allocation[(char_idx, slot)] = "暂缺"

        # Step 3: 组装结果
        result = []
        for idx, char_name, set_name, slots in members_raw:
            entry = {"序号": idx + 1, "角色": char_name, "套装": set_name}
            for slot, gname in slots:
                entry[slot] = gname
                entry[f"{slot}_stat"] = allocation.get((idx, slot), "暂缺")
            # 保证所有槽位都有默认值
            for slot in ("护甲", "护手", "配件1", "配件2"):
                entry.setdefault(slot, "—")
                entry.setdefault(f"{slot}_stat", "暂缺")
            result.append(entry)
        return result


# ============================================================
#  装备计算模块
# ============================================================

class GearCalculator:
    """计算装备总需求"""

    def __init__(self, config: ConfigLoader):
        self.config = config

    def calculate(self, selected_teams):
        """
        计算所选小队的装备总需求。
        规则：同时只上一队，所以不同小队取各装备需求的最大值。
        同一小队内部重复装备如实计数。
        返回: Counter {装备名: 数量}
        """
        if not selected_teams:
            return Counter()

        overall = Counter()
        for team_name in selected_teams:
            team = self.config.teams.get(team_name)
            if not team:
                continue
            team_counts = team.count_gear(self.config.char_gear)
            for gear_name, count in team_counts.items():
                if count > overall.get(gear_name, 0):
                    overall[gear_name] = count
        return overall


# ============================================================
#  GUI 主界面
# ============================================================

class App:
    """主应用程序"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ZMDset — 小队装备管理")
        self.root.minsize(800, 500)

        # ---------- 加载配置 ----------
        config_path = os.path.join(_app_dir(), "setConfig.json")
        try:
            self.config = ConfigLoader(config_path)
        except FileNotFoundError as e:
            tk.messagebox.showerror("错误", str(e))
            sys.exit(1)

        self.calculator = GearCalculator(self.config)

        # 当前在右侧展示的小队名
        self._displayed_team = None

        # ---------- 变量 ----------
        self.team_vars = {}  # team_name -> tk.BooleanVar（是否勾选）
        self.team_widgets = {}  # team_name -> (frame, checkbutton) 用于高亮

        # ---------- 构建界面 ----------
        self._build_ui()

        # ---------- 初始选中第一队 ----------
        if self.config.teams:
            first_team = list(self.config.teams.keys())[0]
            self.team_vars[first_team].set(True)
            self._on_team_toggle(first_team)
            self._highlight_team(first_team)
            self._displayed_team = first_team
            self._refresh_detail(first_team)
            self._refresh_summary()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 根据内容自动调整窗口大小
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        req_w = self.root.winfo_reqwidth()
        req_h = self.root.winfo_reqheight()
        w = min(req_w + 40, int(screen_w * 0.9))
        h = min(req_h + 40, int(screen_h * 0.85))
        x = (screen_w - w) // 2
        y = (screen_h - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        self.root.mainloop()

    # ---- UI 构建 -------------------------------------------------

    def _setup_styles(self):
        """初始化 ttk 样式"""
        style = ttk.Style()
        style.configure("Highlight.TFrame", background="#99c8f0")

    def _build_ui(self):
        """构建主界面布局"""
        # 设置样式
        self._setup_styles()

        # 主 PanedWindow：左右分栏 + 底部
        main_pw = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        main_pw.pack(fill=tk.BOTH, expand=True)

        # 上半部分：左右分栏
        top_pw = ttk.PanedWindow(main_pw, orient=tk.HORIZONTAL)
        main_pw.add(top_pw, weight=3)

        # 底部汇总区
        bottom_frame = ttk.LabelFrame(main_pw, text="装备需求总计（面向所有勾选的小队方案，取各装备需求最大值）", padding=5)
        main_pw.add(bottom_frame, weight=1)

        # --- 左侧：小队方案选择 ---
        left_frame = ttk.LabelFrame(top_pw, text="小队方案（可多选）", padding=5)
        top_pw.add(left_frame, weight=1)

        # 左侧顶部按钮
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(btn_frame, text="全选", command=self._select_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="全不选", command=self._select_none).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="🔄 刷新", command=self._reload_config).pack(side=tk.RIGHT, padx=2)

        # 可滚动的小队列表
        list_container = ttk.Frame(left_frame)
        list_container.pack(fill=tk.BOTH, expand=True)

        self._team_canvas = tk.Canvas(list_container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=self._team_canvas.yview)
        self._team_inner = ttk.Frame(self._team_canvas)

        self._team_inner.bind("<Configure>",
            lambda e: self._team_canvas.configure(scrollregion=self._team_canvas.bbox("all")))
        self._team_canvas.create_window((0, 0), window=self._team_inner, anchor="nw")
        self._team_canvas.configure(yscrollcommand=scrollbar.set)

        self._team_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._team_canvas.bind("<Enter>", lambda e: self._team_canvas.bind_all("<MouseWheel>",
            lambda ev: self._team_canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units")))
        self._team_canvas.bind("<Leave>", lambda e: self._team_canvas.unbind_all("<MouseWheel>"))

        # 填充小队复选框
        for team_name in self.config.teams:
            self._add_team_checkbox(team_name)

        # --- 右侧：队员装备详情（双行网格） ---
        right_frame = ttk.LabelFrame(top_pw, text="队员装备详情（点击左侧小队名查看）", padding=5)
        top_pw.add(right_frame, weight=3)

        # 使用 Canvas + 内部 Frame 实现可滚动的表格
        self._detail_canvas = tk.Canvas(right_frame, highlightthickness=0)
        detail_scroll_y = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=self._detail_canvas.yview)
        detail_scroll_x = ttk.Scrollbar(right_frame, orient=tk.HORIZONTAL, command=self._detail_canvas.xview)
        self._detail_grid = ttk.Frame(self._detail_canvas)

        self._detail_win = self._detail_canvas.create_window((0, 0), window=self._detail_grid, anchor="nw")
        self._detail_canvas.configure(yscrollcommand=detail_scroll_y.set, xscrollcommand=detail_scroll_x.set)

        self._detail_canvas.grid(row=0, column=0, sticky="nsew")
        detail_scroll_y.grid(row=0, column=1, sticky="ns")
        detail_scroll_x.grid(row=1, column=0, columnspan=2, sticky="ew")

        # grid 权重：Canvas 区可拉伸，滚动条固定
        right_frame.grid_rowconfigure(0, weight=1)
        right_frame.grid_columnconfigure(0, weight=1)

        # 防抖：Canvas 大小变化时，内部 Frame 宽度取 max(最小宽度, Canvas 宽度)
        self._detail_resize_id = None
        self._detail_min_width = 760  # 表格最小像素宽度
        def _on_canvas_resize(event):
            if self._detail_resize_id is not None:
                self.root.after_cancel(self._detail_resize_id)
            def _apply():
                self._detail_resize_id = None
                new_w = max(self._detail_min_width, event.width)
                self._detail_canvas.itemconfig(self._detail_win, width=new_w)
                self._detail_canvas.configure(scrollregion=self._detail_canvas.bbox("all"))
            self._detail_resize_id = self.root.after(80, _apply)
        self._detail_canvas.bind("<Configure>", _on_canvas_resize)

        # 鼠标滚轮
        self._detail_canvas.bind("<Enter>", lambda e: self._detail_canvas.bind_all("<MouseWheel>",
            lambda ev: self._detail_canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units")))
        self._detail_canvas.bind("<Leave>", lambda e: self._detail_canvas.unbind_all("<MouseWheel>"))

        # --- 底部：装备总数 ---
        bottom_inner = ttk.Frame(bottom_frame)
        bottom_inner.pack(fill=tk.BOTH, expand=True)

        self._summary_tree = ttk.Treeview(bottom_inner,
            columns=("装备名称", "需要数量", "已有数量", "状态", "装备类型"),
            show="headings", height=8)

        # 可点击排序的列标题
        for col_key in ("装备名称", "需要数量", "已有数量", "状态", "装备类型"):
            self._summary_tree.heading(col_key, text=col_key,
                command=lambda c=col_key: self._sort_summary(c))
        self._summary_tree.column("装备名称", width=180, anchor="center")
        self._summary_tree.column("需要数量", width=80, anchor="center")
        self._summary_tree.column("已有数量", width=80, anchor="center")
        self._summary_tree.column("状态", width=80, anchor="center")
        self._summary_tree.column("装备类型", width=100, anchor="center")

        # 排序状态
        self._summary_sort_col = None
        self._summary_sort_rev = False
        self._summary_rows = []  # [{col: val, "tag": ...}, ...]

        self._summary_tree.tag_configure("satisfied", background="#a3d9b1")
        self._summary_tree.tag_configure("unsatisfied", background="#f2a3a6")

        summary_scroll = ttk.Scrollbar(bottom_inner, orient=tk.VERTICAL, command=self._summary_tree.yview)
        self._summary_tree.configure(yscrollcommand=summary_scroll.set)
        self._summary_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        summary_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 底部标签
        info_label = ttk.Label(bottom_frame, text="提示：同一小队中重复装备如实计数；不同小队间只取最大值（同时仅一队上场）。",
                               foreground="gray")
        info_label.pack(anchor="w", pady=(2, 0))

    def _add_team_checkbox(self, team_name):
        """在左侧列表中添加一个小队的复选框"""
        var = tk.BooleanVar(value=False)
        self.team_vars[team_name] = var

        frame = ttk.Frame(self._team_inner)
        frame.pack(fill=tk.X, padx=2, pady=1)

        cb = ttk.Checkbutton(frame, text=team_name, variable=var,
                             command=lambda tn=team_name: self._on_team_toggle(tn))
        cb.pack(side=tk.LEFT, anchor="w")

        # 点击小队名查看详情
        cb.bind("<Button-1>", lambda e, tn=team_name: self._on_team_click(tn), add="+")

        # 也绑定到 frame 方便点击
        frame.bind("<Button-1>", lambda e, tn=team_name: self._on_team_click(tn))
        for child in frame.winfo_children():
            child.bind("<Button-1>", lambda e, tn=team_name: self._on_team_click(tn), add="+")

        self.team_widgets[team_name] = (frame, cb)

    # ---- 事件处理 -------------------------------------------------

    def _on_team_toggle(self, team_name):
        """复选框勾选状态变化 → 更新底部汇总"""
        self._refresh_summary()

    def _on_team_click(self, team_name):
        """点击小队名 → 右侧展示该队装备"""
        self._highlight_team(team_name)
        self._displayed_team = team_name
        self._refresh_detail(team_name)

    def _highlight_team(self, team_name):
        """高亮当前选中的小队"""
        for tn, (frame, cb) in self.team_widgets.items():
            if tn == team_name:
                frame.configure(style="Highlight.TFrame")
            else:
                frame.configure(style="TFrame")

    def _select_all(self):
        for var in self.team_vars.values():
            var.set(True)
        self._refresh_summary()

    def _select_none(self):
        for var in self.team_vars.values():
            var.set(False)
        self._refresh_summary()

    def _reload_config(self):
        """重新加载 setConfig.json 并刷新全部界面"""
        config_path = os.path.join(_app_dir(), "setConfig.json")
        try:
            self.config = ConfigLoader(config_path)
            self.calculator = GearCalculator(self.config)
        except FileNotFoundError as e:
            tk.messagebox.showerror("错误", str(e))
            return

        # 记录之前的勾选状态
        prev_state = {tn: var.get() for tn, var in self.team_vars.items()}

        # 重建左侧小队列表
        for w in self._team_inner.winfo_children():
            w.destroy()
        self.team_vars.clear()
        self.team_widgets.clear()
        for team_name in self.config.teams:
            self._add_team_checkbox(team_name)
            if prev_state.get(team_name):
                self.team_vars[team_name].set(True)

        # 更新右侧和底部
        if self._displayed_team in self.config.teams:
            self._refresh_detail(self._displayed_team)
        elif self.config.teams:
            first = list(self.config.teams.keys())[0]
            self._displayed_team = first
            self._highlight_team(first)
            self._refresh_detail(first)
        else:
            for w in self._detail_grid.winfo_children():
                w.destroy()
        self._refresh_summary()

    # ---- 刷新视图 -------------------------------------------------

    def _refresh_detail(self, team_name):
        """刷新右侧队员装备详情 —— 双行网格：上行装备名，下行按分配推荐属性"""
        for w in self._detail_grid.winfo_children():
            w.destroy()

        allocated = self.config.allocate_gear_for_team(team_name)
        cols = ("序号", "角色", "套装", "护甲", "护手", "配件1", "配件2")

        # ---------- 表头 ----------
        header_font = ("Microsoft YaHei UI", 9, "bold")
        for ci, col in enumerate(cols):
            lbl = tk.Label(self._detail_grid, text=col, font=header_font,
                           bg="#cccccc", relief="ridge", borderwidth=1)
            lbl.grid(row=0, column=ci, sticky="nsew", padx=0, pady=0)

        # ---------- 数据行（每角色2行） ----------
        row_offset = 1
        gear_keys = ("护甲", "护手", "配件1", "配件2")
        for g in allocated:
            char_row = row_offset
            stat_row = row_offset + 1

            name_values = [g["序号"], g["角色"], g["套装"]] + [g.get(k, "—") for k in gear_keys]
            stat_values = [g.get(f"{k}_stat", "暂缺") for k in gear_keys]

            for ci, val in enumerate(name_values):
                bg = "#e0e0e0" if ci < 3 else "#ffffff"
                if ci < 3:
                    lbl = tk.Label(self._detail_grid, text=str(val), bg=bg, fg="black",
                                   relief="ridge", borderwidth=1, anchor="center")
                    lbl.grid(row=char_row, column=ci, rowspan=2, sticky="nsew", padx=0, pady=0)
                else:
                    lbl_name = tk.Label(self._detail_grid, text=str(val), bg=bg, fg="black",
                                        relief="ridge", borderwidth=1, anchor="center",
                                        wraplength=100, justify="center")
                    lbl_name.grid(row=char_row, column=ci, sticky="nsew", padx=0, pady=0)

                    stat_val = stat_values[ci - 3]
                    stat_color = "gray" if stat_val == "暂缺" else "#0066cc"
                    stat_font = ("Consolas", 9, "bold") if stat_val != "暂缺" else ("Microsoft YaHei UI", 8)
                    lbl_stat = tk.Label(self._detail_grid, text=stat_val, bg="#e5f2e9", fg=stat_color,
                                        font=stat_font, relief="ridge", borderwidth=1, anchor="center")
                    lbl_stat.grid(row=stat_row, column=ci, sticky="nsew", padx=0, pady=0)

            sep = ttk.Separator(self._detail_grid, orient="horizontal")
            sep.grid(row=stat_row + 1, column=0, columnspan=len(cols), sticky="ew", pady=(2, 2))
            row_offset = stat_row + 2

        # 设置列权重，让内容撑开
        for ci in range(len(cols)):
            self._detail_grid.grid_columnconfigure(ci, weight=1)

        # ---------- 小队备注（最末行） ----------
        team = self.config.teams.get(team_name)
        if team and team.comment:
            sep_final = ttk.Separator(self._detail_grid, orient="horizontal")
            sep_final.grid(row=row_offset, column=0, columnspan=len(cols), sticky="ew", pady=(4, 2))
            row_offset += 1
            cmt_lbl = tk.Label(self._detail_grid, text=f"📝 {team.comment}",
                               fg="#555555", font=("Microsoft YaHei UI", 9, "italic"),
                               anchor="w", justify="left")
            cmt_lbl.grid(row=row_offset, column=0, columnspan=len(cols), sticky="w", padx=8, pady=(2, 4))

        # 更新 scrollregion 并重置滚动位置
        self._detail_grid.update_idletasks()
        self._detail_canvas.configure(scrollregion=self._detail_canvas.bbox("all"))
        self._detail_canvas.yview_moveto(0)
        self._detail_canvas.xview_moveto(0)

    def _refresh_summary(self):
        """刷新底部装备汇总（含已有对比，绿底满足 / 红底不足）"""
        selected = [tn for tn, var in self.team_vars.items() if var.get()]
        totals = self.calculator.calculate(selected)
        gear_types = self._build_gear_type_map()

        rows = []
        for gear_name in sorted(totals.keys()):
            need = totals[gear_name]
            owned = self.config.count_owned(gear_name)
            status = "✔ 满足" if owned >= need else "✘ 不足"
            gtype = gear_types.get(gear_name, "—")
            tag = "satisfied" if owned >= need else "unsatisfied"
            rows.append({"装备名称": gear_name, "需要数量": need, "已有数量": owned,
                         "状态": status, "装备类型": gtype, "tag": tag})

        self._summary_rows = rows
        self._summary_sort_col = None
        self._summary_sort_rev = False
        self._repopulate_summary()

    def _repopulate_summary(self):
        """清空并重新填充汇总表格（按当前排序）"""
        for item in self._summary_tree.get_children():
            self._summary_tree.delete(item)
        for row in self._summary_rows:
            self._summary_tree.insert("", tk.END,
                values=(row["装备名称"], row["需要数量"], row["已有数量"], row["状态"], row["装备类型"]),
                tags=(row["tag"],))
        # 更新列标题箭头
        cols = ("装备名称", "需要数量", "已有数量", "状态", "装备类型")
        for c in cols:
            arrow = ""
            if c == self._summary_sort_col:
                arrow = " ▲" if not self._summary_sort_rev else " ▼"
            self._summary_tree.heading(c, text=c + arrow,
                command=lambda cc=c: self._sort_summary(cc))

    def _sort_summary(self, col):
        """点击列标题排序"""
        if self._summary_sort_col == col:
            self._summary_sort_rev = not self._summary_sort_rev
        else:
            self._summary_sort_col = col
            self._summary_sort_rev = False

        key_map = {"装备名称": "装备名称", "需要数量": "需要数量",
                   "已有数量": "已有数量", "状态": "状态", "装备类型": "装备类型"}
        key = key_map.get(col, "装备名称")

        # 数值列按数值排序
        if key in ("需要数量", "已有数量"):
            self._summary_rows.sort(key=lambda r: r[key], reverse=self._summary_sort_rev)
        else:
            self._summary_rows.sort(key=lambda r: r[key], reverse=self._summary_sort_rev)

        self._repopulate_summary()

    def _build_gear_type_map(self):
        """构建装备名 → 装备类型的映射"""
        type_map = {}
        for gear in self.config.char_gear.values():
            type_map[gear.armor] = "护甲"
            type_map[gear.gauntlet] = "护手"
            for acc in (gear.acc1, gear.acc2):
                type_map[acc] = "配件"
        # 已有装备库中未出现在角色装备里的，标记为"已有"
        for ename in self.config.owned_equipment:
            if ename not in type_map:
                type_map[ename] = "已有"
        return type_map

    def _on_close(self):
        self.root.destroy()


# ============================================================
#  入口
# ============================================================

def main():
    App()


if __name__ == "__main__":
    main()
