#!/usr/bin/env python3
"""Scaffold a CAD showcase project: directory layout plus starter templates.

    python scaffold_project.py <project-name> --root <workspace>

Creates the six-stage layout described in SKILL.md and writes four starter
files that encode the conventions worth not re-inventing: parameter sourcing,
the parts spec consumed by glb_stats.py, the model table, and the acceptance
checklist.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DIRS = [
    "src/lib",
    "src/web",
    "STEP",
    "MESH/parts",
    "PDF",
    "RENDER/showcase",
    "checks",
    "tmp",
]

PARAMS_TMPL = '''"""{name} 全部尺寸常量。

坐标约定（写死，后续所有脚本遵守）
    X = 宽度, 居中
    Y = 深度, 居中（负 = 前, 正 = 后）
    Z = 高度, 0 = 底面
单位一律毫米。cadgen 内部是米, 调用时乘 MM。

尺寸来源分四档, 不要混淆:
    [官方] 厂商手册 / 规范原文, 可直接引用
    [图纸] 甲方或设计方图纸标注, 注意保密条款
    [反推] 从照片按已知尺寸标定换算, ±2 mm, 对外只能说「据公开图片重建」
    [估算] 无依据的比例推测, 不能对外说准确
"""

MM = 0.001  # mm -> m, cadgen 调用统一乘它

# ---------------------------------------------------------------- [官方] 总体
# OVERALL_W = 0.0   # 宽
# OVERALL_H = 0.0   # 高
# OVERALL_D = 0.0   # 深

# ---------------------------------------------------------------- [图纸] 分区
# 每个分区都要有: 起始坐标、尺寸、留白

# ---------------------------------------------------------------- [反推] 细节
'''

SPEC_TMPL = {
    "_note": "每件一个 GLB，文件名与 id 一致。glb_stats.py 会实测三角面与包围盒，不要手填。",
    "_explode": "explodeCadMm 用 CAD 坐标（毫米）；前端要的 explodeGlb 由脚本换算，不要写。",
    "model": "",
    "unit": "mm",
    "envelopeCadMm": [0, 0, 0],
    "parts": [
        {
            "id": "example_part",
            "label": "示例零件",
            "color": "#6b7a88",
            "explodeCadMm": [0, 0, 0],
            "layered": False,
            "group": "示例分组",
            "sizeCadMm": [0, 0, 0],
        }
    ],
}

README_TMPL = """# {name} 模型目录

一脚本一入口点。`src/lib/` 放共享尺寸与几何工厂，其余脚本 import 它。

| 脚本 | 产出 | 零件数 | 说明 |
| --- | --- | --- | --- |
| `src/lib/params.py` | — | — | 全部尺寸常量，每条标来源 |
| `src/lib/parts.py` | — | — | 通用几何工厂 |
| `src/lib/layout.py` | — | — | 分区与层高，保证互不干涉 |
| `src/<part>.py` | `STEP/<part>.step` | 1 | 单个零件 |
| `src/assembly.py` | `STEP/<name>.step` | 全部 | 总装 |
| `src/export_parts.py` | `MESH/parts/*.glb` | 全部 | 逐件导出网格 |
| `src/showcase.py` | `MESH/<name>_showcase.html` | 全部 | 单文件展示页 |
| `src/drawing.py` | `PDF/<name>.pdf` | — | 工程图 |

## 约定

- 一个零件一个 STEP，**不要 union 进面板**（一个 STEP 只导出一个 mesh 节点）
- 几何只描述装配态，分解位移写进 `MESH/parts/manifest.json`
- 装顺序：先 `params`，再 `lib`，再零件，最后总装与导出
- 成品不进 `tmp/`
"""

CHECKS_TMPL = """# 验收

详细判据见 `~/.workbuddy/skills/cad-showcase-studio/references/acceptance.md`。

四层，全部失败退出码 1：

1. **几何**：总装外形 vs 官方/图纸（偏差 0.000）· 分解态两两干涉（0）·
   装配态最大搭接（< 阈值）· 关键间隙（> 0）· 刚体平移不变性
2. **页面**（无头浏览器）：零件数 · 拾取命中 · 分解 · 单件高亮且位置不变 ·
   剖切像素不低于原 20% · `clipReport()` 在未剖切时全部为正 · 线框 · 重置 · 无 pageerror · 面板不裁切
3. **工程图**：栅格化后查尺寸线出框、文字被切、视图居中
4. **部署**：本地与线上比字节（**请求必须带随机参数**，CDN 有缓存）

## 一条红线

凡是「前端算一份、GPU 再算一份」的效果（剖切/透明度/雾/线框），
验收必须落在 GPU 真正消费的参数上。只看前端判据会放过整片零件被静默切掉的故障。
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="建 CAD 展示项目骨架")
    ap.add_argument("name", help="项目名，同时作为目录名")
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="工作区根目录")
    ap.add_argument("--force", action="store_true", help="目录非空也继续")
    args = ap.parse_args()

    target = (args.root / args.name).resolve()
    if target.exists() and any(target.iterdir()) and not args.force:
        print(f"{target} 已存在且非空，用 --force 覆盖缺失文件", file=sys.stderr)
        return 1

    for d in DIRS:
        (target / d).mkdir(parents=True, exist_ok=True)

    written = []

    def put(rel: str, text: str) -> None:
        path = target / rel
        if path.exists() and not args.force:
            return
        path.write_text(text, encoding="utf-8")
        written.append(rel)

    put("src/lib/params.py", PARAMS_TMPL.format(name=args.name))
    put("src/README.md", README_TMPL.format(name=args.name))
    put("checks/README.md", CHECKS_TMPL)
    put("tmp/parts_spec.json", json.dumps(SPEC_TMPL, ensure_ascii=False, indent=2) + "\n")
    put("src/lib/__init__.py", "")

    print(f"项目骨架: {target}")
    for d in DIRS:
        print(f"  {d}/")
    if written:
        print("\n写入模板:")
        for w in written:
            print(f"  {w}")
    print(
        "\n下一步：\n"
        "  1. 填 src/lib/params.py，每条尺寸标来源\n"
        "  2. 逐件建 STEP，一个零件一个脚本\n"
        "  3. 逐件导 GLB 到 MESH/parts/，填 tmp/parts_spec.json\n"
        "  4. python ~/.workbuddy/skills/cad-showcase-studio/scripts/glb_stats.py "
        "--dir MESH/parts --spec tmp/parts_spec.json --out MESH/parts/manifest.json\n"
        "  5. 建展示页，跑 checks/"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
