#!/usr/bin/env python3
"""
检查 .pptx 的结构问题 —— 就是 PowerPoint 弹「内容有问题，是否修复」时它在抱怨的那些。

PowerPoint 只说"有问题"，不说是什么问题。这个脚本把已知的几类原因逐个查一遍：

  1. 包里有同名部件（两个 slide3.xml 之类）
  2. 关系指向不存在的部件（悬空引用）
  3. 同一页里形状 id 重复
  4. DrawingML 里 <a:rPr> 等的子元素顺序不合 schema
  5. 图片关系指向的媒体文件缺失
  6. XML 里引用的 r:id 在该部件的 .rels 里不存在（深拷贝形状最容易踩）

用法：
    python -X utf8 check_pptx.py 测试.pptx
    python -X utf8 check_pptx.py 测试.pptx --report   # 打印包内清单，供粘贴排查
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

    # 6. XML 里用到的 r:id 必须在该部件自己的 .rels 里有定义
    R_ATTR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    for entry in sorted(n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)):
        rels_entry = posixpath.join(posixpath.dirname(entry), "_rels",
                                    posixpath.basename(entry) + ".rels")
        defined = set()
        if rels_entry in names:
            try:
                rels_root = etree.fromstring(zf.read(rels_entry))
                defined = {rel.get("Id") for rel in rels_root}
            except etree.XMLSyntaxError:
                pass
        try:
            root = etree.fromstring(zf.read(entry))
        except etree.XMLSyntaxError:
            continue
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            for key, value in el.attrib.items():
                if key.startswith(R_ATTR) and value and value not in defined:
                    problems.append(
                        f"{entry} 引用了不存在的关系 {value}"
                        f"（在 <{el.tag.split('}')[1]}> 的 {key.split('}')[1]} 上）")

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


def report(path):
    """打印一份可粘贴的包内清单 —— 用于检查项全过但 PowerPoint 仍报错时。

    只输出结构信息（部件名、大小、每页形状类型与数量），不含任何文字内容，
    所以可以安全地贴出来。
    """
    zf = zipfile.ZipFile(path)
    names = zf.namelist()

    print(f"文件 {path}")
    print(f"部件总数 {len(names)}")

    slides = sorted(
        (n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
        key=lambda n: int(re.search(r"(\d+)", n).group(1)),
    )
    media = [n for n in names if n.startswith("ppt/media/")]
    print(f"幻灯片 {len(slides)} 个：{', '.join(n.split('/')[-1] for n in slides)}")
    print(f"媒体 {len(media)} 个")

    order = []
    try:
        pres = etree.fromstring(zf.read("ppt/presentation.xml"))
        rels = etree.fromstring(zf.read("ppt/_rels/presentation.xml.rels"))
        by_id = {r.get("Id"): r.get("Target") for r in rels}
        R_ATTR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        for sld in pres.iter(P + "sldId"):
            order.append(by_id.get(sld.get(R_ATTR), "?"))
        print(f"播放顺序 {len(order)} 页：{', '.join(o.split('/')[-1] for o in order)}")
    except (KeyError, etree.XMLSyntaxError) as exc:
        print(f"读取播放顺序失败：{exc}")

    print("\n每页形状：")
    for entry in slides:
        root = etree.fromstring(zf.read(entry))
        kinds = collections.Counter()
        for tag in ("sp", "pic", "graphicFrame", "grpSp", "cxnSp"):
            kinds[tag] = len(list(root.iter(P + tag)))
        rids = {v for el in root.iter() if isinstance(el.tag, str)
                for k, v in el.attrib.items()
                if k.startswith("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}")}
        summary = " ".join(f"{k}={v}" for k, v in kinds.items() if v)
        size = zf.getinfo(entry).file_size
        print(f"  {entry.split('/')[-1]:<14} {size:>7}B  {summary}"
              f"{'  引用=' + ','.join(sorted(rids)) if rids else ''}")


def main():
    parser = argparse.ArgumentParser(description="检查 .pptx 结构问题")
    parser.add_argument("files", nargs="+", help="要检查的 .pptx")
    parser.add_argument("--report", action="store_true",
                        help="打印包内结构清单（不含文字内容，可安全粘贴）")
    args = parser.parse_args()

    if args.report:
        for path in args.files:
            report(path)
        return

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
