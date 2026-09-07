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
  7. 媒体文件的真实格式与扩展名/声明的类型对不上，或 [Content_Types] 没声明
  8. 形状的宽高为负数或 0（缩放算错时会出现，PowerPoint 直接拒绝）

用法：
    python -X utf8 check_pptx.py 测试.pptx
    python -X utf8 check_pptx.py 测试.pptx --report   # 打印包内清单，供粘贴排查
    python -X utf8 check_pptx.py 测试.pptx --media    # 逐张图的格式、像素、DPI
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

VERSION = "2026-09-07j"   # 与 build_deck 同步；跑起来会打印，用于确认版本

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

    # 7. 媒体：真实格式 vs 扩展名 vs [Content_Types] 声明
    problems.extend(check_media(zf, names))

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

        # 8. 非法的宽高。a:ext 的 cx/cy 必须为正；缩放算成 0 或负数时
        #    python-pptx 照写不误，PowerPoint 则直接判定内容有问题。
        for ext in root.iter(A + "ext"):
            for axis in ("cx", "cy"):
                raw = ext.get(axis)
                if raw is not None and int(raw) <= 0:
                    problems.append(f"{entry} 有形状的 {axis}={raw}（宽高必须为正）")

        for tag in ("rPr", "defRPr", "endParaRPr"):
            for node in root.iter(A + tag):
                seen = [c.tag.split("}")[1] for c in node if isinstance(c.tag, str)]
                ranks = [RANK[s] for s in seen if s in RANK]
                if ranks != sorted(ranks):
                    problems.append(f"{entry} <a:{tag}> 子元素顺序不合法：{seen}")

    return problems


# 图片格式的魔数。PowerPoint 按声明的类型去解码，声明与实际不符就解不开，
# 于是"无法读取部分内容并已将这些内容删除"。
MAGIC = [
    (b"\xff\xd8\xff", "jpeg", {"jpg", "jpeg"}),
    (b"\x89PNG\r\n\x1a\n", "png", {"png"}),
    (b"GIF87a", "gif", {"gif"}),
    (b"GIF89a", "gif", {"gif"}),
    (b"BM", "bmp", {"bmp"}),
    (b"II*\x00", "tiff", {"tif", "tiff"}),
    (b"MM\x00*", "tiff", {"tif", "tiff"}),
]


def sniff(blob):
    if blob[4:12] in (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1"):
        return "heic"                      # 苹果设备的默认格式，PowerPoint 不认
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "webp"                      # 旧版 PowerPoint 不认
    for magic, name, _ in MAGIC:
        if blob.startswith(magic):
            return name
    return None


def check_media(zf, names):
    problems = []
    media = [n for n in names if n.startswith("ppt/media/")]
    if not media:
        return problems

    declared = set()
    try:
        ct = etree.fromstring(zf.read("[Content_Types].xml"))
        for node in ct:
            ext = node.get("Extension")
            if ext:
                declared.add(ext.lower())
    except (KeyError, etree.XMLSyntaxError):
        pass

    for entry in media:
        ext = entry.rsplit(".", 1)[-1].lower() if "." in entry else ""
        blob = zf.read(entry)[:16]
        actual = sniff(blob)

        if actual is None:
            problems.append(f"{entry} 不是可识别的图片格式（前 4 字节 {blob[:4]!r}）")
            continue
        if actual in ("heic", "webp"):
            problems.append(f"{entry} 实为 {actual.upper()} 格式，PowerPoint 不支持嵌入，"
                            "需先转成 JPG 或 PNG")
            continue

        allowed = next((exts for _, name, exts in MAGIC if name == actual), set())
        if ext not in allowed:
            problems.append(f"{entry} 实际是 {actual.upper()}，但扩展名是 .{ext} —— "
                            "PowerPoint 会按扩展名解码，解不开就删掉这张图")
        if ext and ext not in declared:
            problems.append(f"[Content_Types].xml 没有声明扩展名 .{ext}，"
                            f"{entry} 无法被识别")
    return problems


def image_size(blob):
    """从原始字节读出像素尺寸与 DPI —— 不依赖 Pillow。"""
    if blob.startswith(b"\x89PNG"):
        w = int.from_bytes(blob[16:20], "big")
        h = int.from_bytes(blob[20:24], "big")
        return w, h, None
    if blob.startswith(b"\xff\xd8"):
        dpi = None
        if blob[6:10] == b"JFIF" and blob[13] == 1:
            dpi = int.from_bytes(blob[14:16], "big")
        i = 2
        while i < len(blob) - 9:
            if blob[i] != 0xFF:
                i += 1
                continue
            marker = blob[i + 1]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h = int.from_bytes(blob[i + 5:i + 7], "big")
                w = int.from_bytes(blob[i + 7:i + 9], "big")
                return w, h, dpi
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + int.from_bytes(blob[i + 2:i + 4], "big")
        return None, None, dpi
    return None, None, None


def media_report(path):
    """逐张图列出格式、字节数、像素尺寸与 DPI。"""
    zf = zipfile.ZipFile(path)
    media = sorted(n for n in zf.namelist() if n.startswith("ppt/media/"))
    print(f"文件 {path}　媒体 {len(media)} 个\n")
    for entry in media:
        blob = zf.read(entry)
        w, h, dpi = image_size(blob)
        kind = sniff(blob[:16]) or "?"
        size = f"{w}x{h}px" if w else "尺寸未识别"
        print(f"  {entry.split('/')[-1]:<16} {kind:<5} {len(blob) / 1024 / 1024:>6.2f}MB"
              f"  {size:<14} DPI={dpi or '未标注'}")


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
    parser.add_argument("--media", action="store_true",
                        help="逐张图列出格式、字节数、像素尺寸与 DPI")
    args = parser.parse_args()
    print(f"check_pptx 版本 {VERSION}（检查项 1-8）")

    if args.media:
        for path in args.files:
            media_report(path)
        return

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
