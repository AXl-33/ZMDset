#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 setConfig.json 同步装备名到 resolution_config.json，新装备入库后运行一次即可，或者可以用于其他数据的同步"""
import json, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE, "setConfig.json"), "r", encoding="utf-8") as f:
    eq = sorted(json.load(f).get("equipment", {}).keys())

with open(os.path.join(BASE, "resolution_config.json"), "r", encoding="utf-8") as f:
    rc = json.load(f)

rc["equipment_names"] = eq

with open(os.path.join(BASE, "resolution_config.json"), "w", encoding="utf-8") as f:
    json.dump(rc, f, ensure_ascii=False, indent=4)

print(f"Synced {len(eq)} equipment names to top level")
