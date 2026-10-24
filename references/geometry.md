# 阶段 1–2：建模与分件导出

## 坐标与单位约定

写进 `src/lib/params.py` 文件头，后续所有脚本遵守：

```
X = 宽度，居中
Y = 深度，居中（负 = 前，正 = 后）
Z = 高度，0 = 底面
单位一律毫米
```

`cadgen` 内部单位是**米**。脚本用毫米时在入口处 `MM = 0.001`，所有 cadgen 调用写成
`bd.Box(w * MM, ...)`，不要出现裸数字。混用的症状是「相机永远调不好」，不会报错。

GLB 导出后是 **Y-up、米、前脸 +Z**。CAD 到 GLB 的换算是
`(x, y, z)_cad → (x, z, −y) / 1000`。这个换算在 Python 侧一次算完写进 `manifest`
的 `explodeGlb`，前端不再做数学。

## 尺寸溯源四档

`params.py` 里每一条常量必须标注来源，`manifest` 里也要能追溯：

| 标签 | 含义 | 能否对外引用 |
|---|---|---|
| `[官方]` | 厂商手册、规范原文 | 可以，注明出处 |
| `[图纸]` | 甲方/设计方图纸标注 | 可以，注意保密条款 |
| `[反推]` | 从照片按已知尺寸标定换算，±2 mm | 不可以，须写「据公开图片重建」 |
| `[估算]` | 无依据的比例推测 | 不可以 |

**只有前两档能说「准确」**，后两档在页面和文章里都要写明是重建。
反过来，把 `[反推]` 当官方值宣传，是这类项目最容易翻车的地方。

### 没有图纸时的反推法

1. 用 PIL 逐像素量（`~/.workbuddy/binaries/python/envs/default/bin/python` 有 PIL，没有 numpy）
2. 先按已知总尺寸初步标定
3. 用**两条互不相关的特征**互校，例如电源中心距 + 蜂窝网节距，两条都落在同一比例才可信
4. 横向与纵向标定可以不一致（截图有拉伸），各自用各自的
5. 拆 PDF 页：`PyPDF2` + macOS `sips -s format png -Z 3000`（pymupdf 常装不上）

实测精度参考：两条特征互校落在 2.374 px/mm，误差 < 0.5%，子尺寸 ±2 mm。

## 零件拆分

**一个零件一个 STEP 文件，一个 STEP 导出为一个 mesh 节点。** 这是硬约束，原因见
`SKILL.md` 第 1 条。

拆分粒度按「能否独立拆卸」定，不按「能否独立制造」：

- 风扇模块、硬盘、电源、GPU 模组、CPU 散热器：必须独立
- 面板上的指示灯、铭牌、螺丝：合进所在零件
- 钣金件（机箱、托盘、面板）：各自独立，它们是分解层次

`src/lib/` 放共享的东西，`src/` 放「一脚本一入口」的构建脚本：

```
src/lib/params.py     全部尺寸常量，标来源
src/lib/parts.py      通用几何工厂（板、孔阵、拉手、齿轮…）
src/lib/layout.py     分区与层高，保证互不干涉
src/lib/modules.py    复杂零件（模组、散热器）的参数化描述
src/<part>.py         每个零件一个入口，import lib 后输出 STEP
```

**内部分区必须与外板分区对齐**，否则零件互相插进去。外板不要做到整机全宽全高，
让开机箱侧板、后上横梁、底盘，靠「贴合」而不是「互穿」。

## 面板类零件的构造法

「整板 + 挖凹槽（功能区）+ 槽内做特征」，不要「先挖窗口再补零件」。
凹槽深度就是外框凸出量，免费得到分区缝与立体感。

坑：**刀具中心要跟着所在面走**。凹槽内特征的中心用槽底坐标，外框上的才用外表面坐标。

## 面数预算

| 对象 | 典型面数 | 备注 |
|---|---|---|
| 机架设备整机 | 40–60 万 | 本项目 575,460 |
| 机械装配 | 30–80 万 | 螺纹与倒角全删 |
| 建筑（一层） | 10–30 万 | 窗框用贴图 |
| 桥梁（全桥） | 50–120 万 | 必须按跨分组 LOD |

降 `mesh_tolerance` 只对曲面细分有效，**对平面多边形孔洞几乎无效**（实测 3e-3→6e-3 只降 9%），
要真减面只能改几何。

## cadgen 环境与命令

```bash
# 固定前缀，kernel 版本别乱升
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.21 ...

# 出图：宽高是 CLI 参数，写进 job 会报 unknown key
PLAYWRIGHT_BROWSERS_PATH=/Users/fah/.cache/ms-playwright \
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.21 \
  cadgen step snapshot --job tmp/views.json --width 2400 --height 1350

# 列装配体 occurrence，用于写 job 里的引用
cadgen step snapshot --mode list STEP/x.step
```

环境坑：

- **`PLAYWRIGHT_BROWSERS_PATH` 必须设**，否则沙箱拦住浏览器安装。做任何快照都不能省。
- **`cadgen glb build` 在本机会崩**：宿主给每个 node 进程注入了 `NODE_OPTIONS=--require=<shim>`，
  拦截 mesh exporter 读包内文件。`env -u` 和改 PATH 都没用。解法是写 Python 脚本，先
  `os.environ.pop("NODE_OPTIONS")` 再 `from cadgen.glb import build`，并设
  `CADGEN_NODE=<真 node 绝对路径>`。纯几何 build/snapshot 不受影响。
- `cadgen` 只有 `read_step`，**没有 DXF/DWG 导入**。图纸必须先转成几何（见
  `object-types.md`）。
- `display.exploded` 只接受 `enabled` + `amount`，**必须写 `"enabled": true`**，
  只给 amount 会被静默忽略。`display` 与 `selection` 都是 job 级键，同批不同设置要用
  `{"jobs":[...]}` 数组。

## 判断「改好了没有」

工具（含 AI 生成脚本）说改成功不算数。三个客观判据：

1. **比 md5**：改前改后两次产物字节相同 = 实际没改
2. **量包围盒**：刚体平移后体积与边长必须与原件一致
3. **看导出文件存在且时间戳更新**，别看日志

这次项目里「爆炸参数被静默忽略」就是靠 md5 相同才发现的，出图「看着对」骗过了眼睛。
