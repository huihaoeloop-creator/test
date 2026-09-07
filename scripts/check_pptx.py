#!/usr/bin/env python3
"""
检查 .pptx 的结构问题 —— 就是 PowerPoint 弹「内容有问题，是否修复」时它在抱怨的那些。

PowerPoint 只说"有问题"，不说是什么问题。这个脚本把已知的几类原因逐个查一遍：

  1. 包里有同名部件（两个 slide3.xml 之类）
  2. 关系指向不存在的部件（悬空引用）
  3. 同一页里形状 id 重复
  4. DrawingML 里 <a:rPr> 等的子元素顺序不合 schema
  5. 图片关系指向的媒体文件缺失

用法：
    python -X utf8 check_pptx.py 测试.pptx
"""
from __future__ import annotations

import argparse
import collections
import posixpath
import re
import sys
import zipfile

try:
    from lxml import etree
except ImportError:
    sys.exit("请先安装依赖：pip install python-pptx")

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# CT_TextCharacterProperties 的合法子元素顺序
RPR_ORDER = [
    "ln", "noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill",
    "effectLst", "effectDag", "highlight", "uLnTx", "uLn", "uFillTx", "uFill",
    "latin", "ea", "cs", "sym", "hlinkClick", "hlinkMouseOver", "rtl", "extLst",
]
RANK = {name: i for i, name in enumerate(RPR_ORDER)}


def check(path):
    problems = []
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        return [f"{path} 不是有效的 zip —— 文件本身损坏或没写完"]

    names = zf.namelist()

    # 1. 同名部件
    for name, count in collections.Counter(names).items():
        if count > 1:
            problems.append(f"包里有 {count} 个同名部件：{name}")

    # 2 & 5. 悬空引用
    for entry in names:
        if not entry.endswith(".rels"):
            continue
        base = posixpath.dirname(posixpath.dirname(entry))
        try:
            root = etree.fromstring(zf.read(entry))
        except etree.XMLSyntaxError as exc:
            problems.append(f"{entry} XML 解析失败：{exc}")
            continue
        for rel in root:
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target", "")
            resolved = posixpath.normpath(posixpath.join(base, target))
            if resolved not in names:
                problems.append(f"{entry} 指向不存在的部件：{target}")

    # 3 & 4. 逐页检查
    for entry in sorted(n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)):
        try:
            root = etree.fromstring(zf.read(entry))
        except etree.XMLSyntaxError as exc:
            problems.append(f"{entry} XML 解析失败：{exc}")
            continue

        ids = [el.get("id") for el in root.iter(P + "cNvPr")]
        for value, count in collections.Counter(ids).items():
            if count > 1:
                problems.append(f"{entry} 形状 id {value} 出现 {count} 次（同页必须唯一）")

        for tag in ("rPr", "defRPr", "endParaRPr"):
            for node in root.iter(A + tag):
                seen = [c.tag.split("}")[1] for c in node if isinstance(c.tag, str)]
                ranks = [RANK[s] for s in seen if s in RANK]
                if ranks != sorted(ranks):
                    problems.append(f"{entry} <a:{tag}> 子元素顺序不合法：{seen}")

    return problems


def main():
    parser = argparse.ArgumentParser(description="检查 .pptx 结构问题")
    parser.add_argument("files", nargs="+", help="要检查的 .pptx")
    args = parser.parse_args()

    failed = False
    for path in args.files:
        problems = check(path)
        if problems:
            failed = True
            print(f"\n{path}　发现 {len(problems)} 个问题：")
            for item in problems:
                print(f"  !! {item}")
        else:
            print(f"{path}　已知结构检查全部通过")

    if failed:
        print("\n把上面这段发回给我。若全部通过而 PowerPoint 仍要求修复，"
              "说明是这个脚本还没覆盖的原因，需要看文件本身。")
        sys.exit(1)


if __name__ == "__main__":
    main()
