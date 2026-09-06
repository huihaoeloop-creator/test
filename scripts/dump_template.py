#!/usr/bin/env python3
"""
Dump a PowerPoint template's structure: slide size, masters, layouts, and every
placeholder's index, type, name and geometry.

The placeholder idx values are the contract between the template and any script
that fills it — python-pptx addresses placeholders by idx, not by position, so
this listing is what a filling script is written against.

Usage:
    pip install python-pptx
    python dump_template.py 模板.potx            # 人类可读
    python dump_template.py 模板.potx --json     # 机器可读，供灌装脚本使用
"""
import argparse
import json
import sys

try:
    from pptx import Presentation
    from pptx.util import Emu
except ImportError:
    sys.exit("请先安装依赖：pip install python-pptx")


def inches(value):
    """Placeholders may inherit geometry from the master, leaving these unset."""
    return round(Emu(value).inches, 2) if value is not None else None


def describe(shape):
    fmt = shape.placeholder_format
    # str(type) looks like "BODY (2)"; keep the readable half.
    kind = str(fmt.type).split(" (")[0] if fmt.type is not None else "UNKNOWN"
    return {
        "idx": fmt.idx,
        "type": kind,
        "name": shape.name,
        "left_in": inches(shape.left),
        "top_in": inches(shape.top),
        "width_in": inches(shape.width),
        "height_in": inches(shape.height),
    }


def collect(path):
    prs = Presentation(path)
    report = {
        "file": path,
        "slide_width_in": inches(prs.slide_width),
        "slide_height_in": inches(prs.slide_height),
        "masters": [],
    }

    for m_i, master in enumerate(prs.slide_masters):
        master_info = {"index": m_i, "name": master.name, "layouts": []}
        for l_i, layout in enumerate(master.slide_layouts):
            master_info["layouts"].append(
                {
                    "index": l_i,
                    "name": layout.name,
                    "placeholders": [describe(ph) for ph in layout.placeholders],
                }
            )
        report["masters"].append(master_info)

    # A .potx normally carries no slides; a .pptx used as a template does.
    report["existing_slides"] = [
        {"index": i, "layout": s.slide_layout.name} for i, s in enumerate(prs.slides)
    ]
    return report


def render(report):
    ratio = report["slide_width_in"] / report["slide_height_in"]
    shape = "16:9" if abs(ratio - 16 / 9) < 0.05 else "4:3" if abs(ratio - 4 / 3) < 0.05 else f"{ratio:.2f}:1"
    print(f"文件: {report['file']}")
    print(f"页面: {report['slide_width_in']} x {report['slide_height_in']} in  ({shape})")

    for master in report["masters"]:
        print(f"\n母版 {master['index']}: {master['name']}")
        for layout in master["layouts"]:
            print(f"\n  版式 [{layout['index']}] {layout['name']}")
            if not layout["placeholders"]:
                print("      (无占位符)")
            for ph in layout["placeholders"]:
                pos = (
                    f"({ph['left_in']}, {ph['top_in']}) {ph['width_in']}x{ph['height_in']} in"
                    if ph["left_in"] is not None
                    else "(几何继承自母版)"
                )
                print(f"      idx={ph['idx']:<3} {ph['type']:<16} {ph['name']!r:<32} {pos}")

    if report["existing_slides"]:
        print(f"\n模板内已有 {len(report['existing_slides'])} 页幻灯片:")
        for s in report["existing_slides"]:
            print(f"  第 {s['index'] + 1} 页 -> 版式 {s['layout']!r}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("template", help=".potx 或 .pptx 路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    data = collect(args.template)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        render(data)
