#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
格式化 set.config：
  1. 统一 4 空格缩进
  2. 对 characters / equipment / teams 三大类中的条目按名称排序
"""

import json
import os
from collections import OrderedDict

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setConfig.json")


def sort_section(data, key):
    """对 data[key] 中的条目按键名排序（保留其他字段原序）"""
    if key not in data:
        return
    section = data[key]
    if isinstance(section, dict):
        data[key] = OrderedDict(sorted(section.items(), key=lambda kv: kv[0]))


def main():
    with open(FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 对三个大类分别排序
    for section in ("characters", "equipment", "teams"):
        sort_section(data, section)

    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    print(f"✅ {FILE} 格式化并排序完成")


if __name__ == "__main__":
    main()
