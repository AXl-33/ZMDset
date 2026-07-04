# ZMDset — 小队装备管理工具

可视化小队装备需求分析工具，适用于游戏中多名角色组成小队的配装管理场景。

## 功能

- **小队方案管理** — 左侧多选队伍，右侧实时展示 4 人装备详情
- **智能装备分配** — 同队多人需求同一装备时，按属性和（a+b+c）从高到低依次分配
- **已有/需求对比** — 底部汇总表对比装备需求量与库存量，满足绿底、不足红底
- **多套装备支持** — 同一角色可定义多套装备方案，队伍可按套装配队
- **队伍备注** — 每支队伍支持 comment 说明字段，展示于详情最末行
- **点击排序** — 点击汇总表列标题可按该列升/降序排列
- **一键刷新** — 修改配置文件后点击 🔄 按钮即可同步，无需重启
- **格式化脚本** — `format_config.py` 自动格式化 JSON 并对条目字典序排序

## 文件结构

```
ZMDset/
├── ZMDset.py          # 主程序（GUI）
├── setConfig.json      # 配置文件
├── format_config.py    # 格式化 & 排序脚本
├── build.py            # PyInstaller 打包脚本
├── .gitignore
└── README.md
```

## 配置文件格式

`setConfig.json` 为标准 JSON，包含四个顶层字段：

```json
{
    "characters": {
        "角色名": [
            {
                "set": "套装名",
                "armor": "护甲名",
                "gauntlet": "护手名",
                "accessory1": "配件名",
                "accessory2": "配件名"
            }
        ]
    },
    "equipment": {
        "装备名": { "a": 6, "b": 5, "c": 3 }
    },
    "teams": {
        "小队名": {
            "comment": "备注说明",
            "members": [
                { "character": "角色名", "set": "套装名" }
            ]
        }
    }
}
```

### 装备字段

| 键 | 类型 | 说明 |
|------|------|------|
| `a`, `b`, `c` | 整数 | 装备的三项属性值 |
| 单件格式 | `{ "a": 6, "b": 5, "c": 3 }` | 拥有一件 |
| 多件格式 | `[{ "a": 6, "b": 5, "c": 3 }, { "a": 5, "b": 6, "c": 4 }]` | 拥有多件（自动按 a+b+c 排序分配） |

## 运行

```powershell
python ZMDset.py
```

依赖仅为 Python 3 标准库（`tkinter`, `json`, `collections`），无需额外安装。

## 格式化配置

```powershell
python format_config.py
```

自动对 `setConfig.json` 进行 4 空格缩进，并按 `characters`、`equipment`、`teams` 条目名排序。

## 构建发布版本

生成独立 `.exe` 文件，分发给未安装 Python 的用户：

```powershell
pip install pyinstaller
python build.py
```

输出目录 `dist/ZMDset/` 包含：

| 文件 | 说明 |
|------|------|
| `ZMDset.exe` | 主程序（双击运行） |
| `setConfig.json` | 配置文件（用户可编辑，点击 🔄 刷新同步） |

将整个 `dist/ZMDset/` 文件夹打包为 zip 即可分发。
