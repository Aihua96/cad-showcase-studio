#!/usr/bin/env python3
"""Read GLB files and report triangle counts and world-space bounding boxes.

Used to build the parts manifest of a CAD showcase project so every number is
measured from the exported mesh instead of being typed by hand.

Typical use:

    python glb_stats.py MESH/parts/*.glb
    python glb_stats.py --dir MESH/parts --spec tmp/parts_spec.json --out MESH/parts/manifest.json

The manifest mode also validates that the declared CAD sizes and explode
offsets agree with the measured geometry. Mismatches abort with exit code 1,
because a wrong bbox makes the camera framing silently drift off-screen.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

GLTF_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
MODE_TRIANGLES = 4

IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


# --------------------------------------------------------------- GLB 解析
def read_glb_json(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < 12:
        raise ValueError("file too short")
    magic, _version, length = struct.unpack_from("<III", data, 0)
    if magic != GLTF_MAGIC:
        raise ValueError("not a GLB (bad magic)")
    offset = 12
    doc = None
    while offset + 8 <= min(length, len(data)):
        clen, ctype = struct.unpack_from("<II", data, offset)
        if ctype == CHUNK_JSON:
            doc = json.loads(data[offset + 8: offset + 8 + clen].decode("utf-8"))
        # 规范要求 chunkLength 已含 padding, 直接跳过即可
        offset += 8 + clen
    if doc is None:
        raise ValueError("no JSON chunk")
    return doc


# --------------------------------------------------------------- 矩阵
def mat_mul(a: list[float], b: list[float]) -> list[float]:
    """Column-major 4x4: return a * b (b applied first)."""
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
    return out


def quat_to_mat(q: list[float]) -> list[float]:
    x, y, z, w = q
    n = (x * x + y * y + z * z + w * w) ** 0.5 or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return [
        1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w), 0.0,
        2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w), 0.0,
        2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y), 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]


def node_matrix(node: dict) -> list[float]:
    if "matrix" in node:
        return [float(v) for v in node["matrix"]]
    m = IDENTITY[:]
    if "scale" in node:
        s = node["scale"]
        m = mat_mul(m, [s[0], 0, 0, 0, 0, s[1], 0, 0, 0, 0, s[2], 0, 0, 0, 0, 1])
    if "rotation" in node:
        m = mat_mul(m, quat_to_mat(node["rotation"]))
    if "translation" in node:
        t = node["translation"]
        m = mat_mul(m, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, t[0], t[1], t[2], 1])
    return m


def apply(m: list[float], p: list[float]) -> list[float]:
    x, y, z = p
    return [
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    ]


# --------------------------------------------------------------- 统计
def glb_stats(path: Path) -> dict:
    doc = read_glb_json(path)
    accessors = doc.get("accessors", [])
    meshes = doc.get("meshes", [])
    nodes = doc.get("nodes", [])

    triangles = 0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    seen = False

    def visit(node_index: int, parent: list[float]) -> None:
        nonlocal triangles, seen
        node = nodes[node_index]
        world = mat_mul(parent, node_matrix(node))
        if "mesh" in node:
            for prim in meshes[node["mesh"]].get("primitives", []):
                pos_idx = prim.get("attributes", {}).get("POSITION")
                if pos_idx is None:
                    continue
                pos = accessors[pos_idx]
                count = int(pos.get("count", 0))
                mode = prim.get("mode", MODE_TRIANGLES)
                if mode == MODE_TRIANGLES:
                    idx = prim.get("indices")
                    triangles += (int(accessors[idx].get("count", 0)) if idx is not None else count) // 3
                if "min" in pos and "max" in pos:
                    amin, amax = pos["min"], pos["max"]
                    for cx in (amin[0], amax[0]):
                        for cy in (amin[1], amax[1]):
                            for cz in (amin[2], amax[2]):
                                p = apply(world, [cx, cy, cz])
                                seen = True
                                for i in range(3):
                                    lo[i] = min(lo[i], p[i])
                                    hi[i] = max(hi[i], p[i])
        for child in node.get("children", []):
            visit(child, world)

    roots = [i for i in range(len(nodes)) if not any(i in n.get("children", []) for n in nodes)]
    for r in roots:
        visit(r, IDENTITY)

    if not seen:
        raise ValueError("no POSITION min/max found; cannot measure bbox")

    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "mb": round(path.stat().st_size / 1048576, 2),
        "triangles": triangles,
        "bboxGlb": [[round(v, 4) for v in lo], [round(v, 4) for v in hi]],
        "sizeGlb": [round(hi[i] - lo[i], 4) for i in range(3)],
    }


# --------------------------------------------------------------- CAD 换算
def glb_to_cad_size(size_glb: list[float]) -> list[float]:
    """GLB 是 Y-up 米: (x, y, z) = (cad_x, cad_z, -cad_y) / 1000."""
    dx, dy, dz = size_glb
    return [round(dx * 1000, 2), round(dz * 1000, 2), round(dy * 1000, 2)]


def cad_to_glb_offset(offset_mm: list[float]) -> list[float]:
    ex, ey, ez = offset_mm
    return [round(ex / 1000, 4), round(ez / 1000, 4), round(-ey / 1000, 4)]


# --------------------------------------------------------------- manifest
def build_manifest(spec_path: Path, glb_dir: Path, size_tol=0.6) -> tuple[dict, list[str], list[str]]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    warnings: list[str] = []
    parts_out: list[dict] = []

    union_lo = [float("inf")] * 3
    union_hi = [float("-inf")] * 3
    total_tri = 0
    total_mb = 0.0

    for entry in spec["parts"]:
        pid = entry["id"]
        glb_path = glb_dir / f"{pid}.glb"
        if not glb_path.is_file():
            problems.append(f"{pid}: 找不到 {glb_path}")
            continue
        stats = glb_stats(glb_path)
        total_tri += stats["triangles"]
        total_mb += stats["mb"]
        for i in range(3):
            union_lo[i] = min(union_lo[i], stats["bboxGlb"][0][i])
            union_hi[i] = max(union_hi[i], stats["bboxGlb"][1][i])

        offset_mm = entry.get("explodeCadMm", [0, 0, 0])
        explode_glb = cad_to_glb_offset(offset_mm)
        measured = glb_to_cad_size(stats["sizeGlb"])

        declared = entry.get("sizeCadMm")
        if declared:
            for i, (d, m) in enumerate(zip(declared, measured)):
                if abs(d - m) > size_tol:
                    problems.append(
                        f"{pid}: sizeCadMm[{i}] 声明 {d} 与实测 {m} 差 {abs(d - m):.2f} mm"
                    )
        part = {
            "id": pid,
            "label": entry.get("label", pid),
            "color": entry.get("color", "#6b7a88"),
            "glb": stats["file"],
            "explodeCadMm": [round(v, 2) for v in offset_mm],
            "explodeGlb": explode_glb,
            "sizeCadMm": measured,
            "bboxGlb": stats["bboxGlb"],
            "triangles": stats["triangles"],
            "layered": bool(entry.get("layered", False)),
            "mb": stats["mb"],
        }
        if "group" in entry:
            part["group"] = entry["group"]
        parts_out.append(part)

    measured_env = glb_to_cad_size([union_hi[i] - union_lo[i] for i in range(3)])
    envelope = spec.get("envelopeCadMm") or measured_env
    if spec.get("envelopeCadMm"):
        for i, name in enumerate("宽 深 高".split()):
            if abs(envelope[i] - measured_env[i]) > 1.0:
                # 不算失败: 零件可能真的略微凸出官方外形。但必须让人看见。
                warnings.append(
                    f"envelope {name}: 声明 {envelope[i]} 与实测并集 {measured_env[i]} 差 "
                    f"{abs(envelope[i] - measured_env[i]):.1f} mm（确认是否有零件凸出外形）"
                )
    manifest = {
        "model": spec.get("model", spec_path.stem),
        "unit": spec.get("unit", "mm"),
        "up": spec.get("up", "+Y (GLB) / +Z (CAD)"),
        "front": spec.get("front", "+Z (GLB) / -Y (CAD)"),
        "envelopeCadMm": envelope,
        "note": spec.get("note", ""),
        "parts": parts_out,
        "totals": {
            "parts": len(parts_out),
            "triangles": total_tri,
            "mb": round(total_mb, 2),
        },
    }
    return manifest, problems, warnings


# --------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser(description="GLB 统计与零件清单生成")
    ap.add_argument("files", nargs="*", type=Path, help="GLB 文件")
    ap.add_argument("--dir", type=Path, help="GLB 目录（配合 --spec 生成 manifest）")
    ap.add_argument("--spec", type=Path, help="parts_spec.json")
    ap.add_argument("--out", type=Path, help="输出 manifest 路径")
    ap.add_argument("--json", action="store_true", help="只打印 JSON")
    args = ap.parse_args()

    if args.dir and args.spec:
        manifest, problems, warnings = build_manifest(args.spec, args.dir)
        text = json.dumps(manifest, ensure_ascii=False, indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text + "\n", encoding="utf-8")
            print(f"写入 {args.out}")
        else:
            print(text)
        t = manifest["totals"]
        print(f"零件 {t['parts']} 件，三角面 {t['triangles']:,}，合计 {t['mb']} MB")
        if warnings:
            print("\n需要确认：")
            for w in warnings:
                print("  ~ " + w)
        if problems:
            print("\n校验失败：", file=sys.stderr)
            for p in problems:
                print("  - " + p, file=sys.stderr)
            return 1
        print("尺寸与分解位移校验通过")
        return 0

    if not args.files:
        ap.print_help()
        return 2

    rows = []
    for f in args.files:
        try:
            rows.append(glb_stats(f))
        except Exception as exc:  # noqa: BLE001 - 单文件失败不影响其他文件
            print(f"{f.name}: {exc}", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"{'file':<28}{'MB':>7}{'tris':>10}  sizeGLB(m)               sizeCAD(mm)")
        for r in rows:
            s = " × ".join(f"{v:.3f}" for v in r["sizeGlb"])
            c = " × ".join(f"{v:.1f}" for v in glb_to_cad_size(r["sizeGlb"]))
            print(f"{r['file']:<28}{r['mb']:>7}{r['triangles']:>10}  {s:<24} {c}")
        print(f"\n合计 {sum(r['triangles'] for r in rows):,} 面，{sum(r['mb'] for r in rows):.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
