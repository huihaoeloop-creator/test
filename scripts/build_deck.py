#!/usr/bin/env python3
"""
按陈主管的 .potx 模板生成设计提案 PPT。

设计约束（来自模板实测，见 dump_template.py 的输出）：

  * 模板是 Office 标准 11 版式的汉化版，没有为设计提案定制的版式。
  * 全模板只有版式 [8] 带一个 PICTURE 占位符，一页只能放一张图。
    python-pptx 只在 PP_PLACEHOLDER.PICTURE 上提供 insert_picture()，
    "内容"(OBJECT) 占位符没有这个方法，所以多图页一律走 add_picture()
    按坐标铺网格。
  * 网格的边界不写死，而是从版式 [1] 的标题框和内容框读出来，
    这样模板改了边距，生成的页面跟着走。
  * 推荐页（一款一页、正反两图）复刻模板参考页：两个文本框整段拷贝 XML，
    只换文字，格式一个字节不改；两张图等比放进模板记下的矩形。

绝不新建版式、绝不改母版 —— 只用现成版式和自己画的形状。

用法：
    pip install python-pptx
    python -X utf8 build_deck.py brief.json -t 模板.potx -o 提案.pptx
    python -X utf8 build_deck.py --sample-brief > brief.json   # 生成示例输入
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import math
import re
import sys
import zipfile
from pathlib import Path

try:
    from lxml import etree
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.oxml.ns import qn
    from pptx.util import Emu, Inches, Pt
except ImportError:
    sys.exit("请先安装依赖：pip install python-pptx")


# --------------------------------------------------------------------------
# 打开模板
# --------------------------------------------------------------------------

# python-pptx 只接受 "presentation" 主部件类型。.potx 的包结构与 .pptx 完全一致，
# 只有这一个类型串不同，所以在内存里改写后再加载，磁盘上的模板不动。
# 每次跑都打出来 —— 这个工具改得频繁，出问题时第一件事是确认跑的是哪一版。
VERSION = "2026-09-07g"

MAIN_PART_CT = re.compile(r'ContentType="[^"]*(?:presentationml|ms-powerpoint)[^"]*\.main\+xml"')
PRESENTATION_CT = (
    'ContentType="application/vnd.openxmlformats-officedocument'
    '.presentationml.presentation.main+xml"'
)


def open_template(path):
    try:
        return Presentation(path)
    except ValueError as exc:
        if "not a PowerPoint file" not in str(exc):
            raise

    buf = io.BytesIO()
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = MAIN_PART_CT.sub(PRESENTATION_CT, data.decode("utf-8")).encode("utf-8")
            dst.writestr(item, data)
    buf.seek(0)
    return Presentation(buf)


def drop_slides(prs, slide_ids):
    """删掉指定的幻灯片。python-pptx 没有公开的删页 API，只能直接动 sldIdLst。

    必须在生成内容页 *之后* 调用。python-pptx 按现存页数给新页命名，先删页
    会让新页拿到已被保留的模板页的部件名（slide3.xml），包里出现同名部件，
    文件就坏了。
    """
    id_list = prs.slides._sldIdLst
    for slide_id in slide_ids:
        prs.part.drop_rel(slide_id.get(qn("r:id")))
        id_list.remove(slide_id)


def move_to_end(prs, slide_id):
    """把尾页挪回最后 —— 新建的页只会追加到末尾，会插到它后面。"""
    id_list = prs.slides._sldIdLst
    id_list.remove(slide_id)
    id_list.append(slide_id)


# --------------------------------------------------------------------------
# 版式与几何
# --------------------------------------------------------------------------

# 索引对应 Office 标准版式集；模板若重排过版式，用 --layout-map 覆盖。
LAYOUTS = {
    "cover": 0,          # 标题幻灯片
    "title_content": 1,  # 标题和内容
    "section": 2,        # 节标题
    "title_only": 5,     # 仅标题
    "blank": 6,          # 空白
}


class Geometry:
    """从模板版式读出的可用区域，而不是写死的边距。"""

    def __init__(self, prs, layouts):
        self.prs = prs
        content_layout = prs.slide_layouts[layouts["title_content"]]
        title_ph = content_layout.placeholders[0]
        body_ph = content_layout.placeholders[1]

        self.title = (title_ph.left, title_ph.top, title_ph.width, title_ph.height)
        self.body = (body_ph.left, body_ph.top, body_ph.width, body_ph.height)

    @property
    def body_box(self):
        return self.body

    def title_only_body(self):
        """仅标题版式上，标题以下到正文区底部的整块区域。"""
        t_left, t_top, t_width, t_height = self.title
        b_left, b_top, b_width, b_height = self.body
        top = t_top + t_height + Inches(0.15)
        return (b_left, top, b_width, (b_top + b_height) - top)


def grid_cells(box, count, max_cols=3, gap=Inches(0.16)):
    """把一块区域切成尽量方正的网格，返回每格的 (left, top, w, h)。"""
    left, top, width, height = box
    cols = max(1, min(max_cols, count))
    rows = math.ceil(count / cols)

    cell_w = (width - gap * (cols - 1)) // cols
    cell_h = (height - gap * (rows - 1)) // rows

    for i in range(count):
        r, c = divmod(i, cols)
        yield (left + c * (cell_w + gap), top + r * (cell_h + gap), cell_w, cell_h)


# --------------------------------------------------------------------------
# 文本
# --------------------------------------------------------------------------


def sample_typography(prs, index=1):
    """列出参考页上出现的字体与字号，并挑一个当正文。

    必须在删页之前调用。挑选规则是取**最小**的字号：一页上正文几乎总是最小
    的那一档，而标题、页码等更大或更特殊。参考页若只有标题，挑出来的自然
    就是标题字号 —— 所以把完整清单打印出来，让人能看见并用 --body-size 覆盖。

    字号可能继承自版式/母版而没写在 run 上，run 上取不到时退回扫描 XML 里的
    defRPr。中文字体在 <a:ea>，run.font.name 只读 <a:latin>，两个都要看。
    """
    slides = list(prs.slides)
    if index >= len(slides):
        return {"font": None, "size": None, "found": [],
                "source": f"模板没有第 {index + 1} 页"}

    slide = slides[index]
    fonts, sizes, pairs = [], [], []

    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        for para in shape.text_frame.paragraphs:
            for run in para.runs:
                name = run.font.name
                rPr = run._r.find(qn("a:rPr"))
                if rPr is not None:
                    ea = rPr.find(qn("a:ea"))
                    if ea is not None and ea.get("typeface"):
                        name = ea.get("typeface")
                if name:
                    fonts.append(name)
                if run.font.size:
                    sizes.append(run.font.size)
                if name or run.font.size:
                    text = run.text.strip()
                    pairs.append((name, run.font.size, text[:22]))

    if not sizes:
        for node in slide.element.iter(qn("a:defRPr")):
            raw = node.get("sz")
            if raw:
                size = Pt(int(raw) / 100)
                sizes.append(size)
                pairs.append((None, size, "(继承自版式)"))
    if not fonts:
        for tag in ("a:latin", "a:ea"):
            for node in slide.element.iter(qn(tag)):
                if node.get("typeface"):
                    fonts.append(node.get("typeface"))

    font = max(set(fonts), key=fonts.count) if fonts else None
    size = min(sizes) if sizes else None       # 正文 = 页面上最小的一档
    source = (f"模板第 {index + 1} 页" if (font or size)
              else f"模板第 {index + 1} 页没有可采样的文字")
    return {"font": font, "size": size, "found": pairs, "source": source}


def theme_font(prs):
    """取模板主题的正文字体，让自己画的文本框跟模板保持一致。"""
    try:
        theme = prs.slide_masters[0].part.part_related_by(
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
        )
        root = etree.fromstring(theme.blob)
        minor = root.find(".//" + qn("a:fontScheme") + "/" + qn("a:minorFont"))
        if minor is None:
            return None
        ea = minor.find(qn("a:ea"))
        latin = minor.find(qn("a:latin"))
        for node in (ea, latin):
            if node is not None and node.get("typeface"):
                return node.get("typeface")
    except Exception:
        pass
    return None


def style_run(run, font=None, size=None, bold=False, color=None):
    run.font.bold = bold
    if size is not None:
        run.font.size = size
    if color is not None:
        run.font.color.rgb = color
    if font:
        run.font.name = font
        # font.name 只写 <a:latin>；中文走 <a:ea>，不设的话会掉回默认字体。
        rPr = run._r.get_or_add_rPr()
        for tag in ("a:ea", "a:cs"):
            node = rPr.find(qn(tag))
            if node is None:
                node = etree.SubElement(rPr, qn(tag))
            node.set("typeface", font)


def textbox(slide, box, lines, font=None, size=Pt(11), color=None, align=PP_ALIGN.LEFT,
            anchor=MSO_ANCHOR.TOP):
    """在指定位置放一个文本框。lines 里每项是 (文本, 是否加粗)。"""
    left, top, width, height = box
    shape = slide.shapes.add_textbox(left, top, width, height)
    frame = shape.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor

    for i, item in enumerate(lines):
        text, bold = item if isinstance(item, tuple) else (item, False)
        para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        para.alignment = align
        style_run(para.add_run(), font=font, size=size, bold=bold, color=color)
        para.runs[0].text = str(text)
    return shape


def fill_placeholder(slide, idx, lines, font=None, size=None):
    """往现成占位符里灌文本 —— 字号、颜色全部继承模板。"""
    try:
        ph = slide.placeholders[idx]
    except KeyError:
        return None

    frame = ph.text_frame
    frame.clear()
    for i, item in enumerate(lines):
        text, level = item if isinstance(item, tuple) else (item, 0)
        para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        para.level = level
        run = para.add_run()
        run.text = str(text)
        if font or size:
            # size=None 时保持继承：标题该走模板的层级字号，只有正文跟参考页。
            style_run(run, font=font, size=size)
    return ph


# --------------------------------------------------------------------------
# 图片
# --------------------------------------------------------------------------


def place_contained(slide, image_path, cell, caption=None, font=None, size=Pt(9)):
    """把图按原比例缩放后居中放进格子；有 caption 就在下方留一行。"""
    left, top, width, height = cell
    caption_h = Inches(0.26) if caption else 0
    avail_h = height - caption_h

    # 先按原始尺寸插入，读出宽高比，再等比缩放 —— 这样不必依赖 Pillow。
    pic = slide.shapes.add_picture(str(image_path), left, top)
    scale = min(width / pic.width, avail_h / pic.height)
    pic.width = int(pic.width * scale)
    pic.height = int(pic.height * scale)
    pic.left = left + (width - pic.width) // 2
    pic.top = top + (avail_h - pic.height) // 2

    if caption:
        textbox(
            slide,
            (left, top + avail_h, width, caption_h),
            [caption],
            font=font,
            size=size,
            color=RGBColor(0x80, 0x80, 0x80),
            align=PP_ALIGN.CENTER,
        )
    return pic


# --------------------------------------------------------------------------
# 推荐页：照抄模板参考页的版面
# --------------------------------------------------------------------------


class ReferencePage:
    """把模板参考页拆成「两个文本框 + 两个图片框」，供推荐页复刻。

    文本框整段 XML 深拷贝，只替换文字 —— 比照着导出的字号重建更可靠：
    模板里 "Style: 001" 那个框的字体和字号是继承来的，没有显式值可抄，
    重建必然猜错，拷贝则原样带过来。

    图片不拷贝，只记下矩形，然后把实际素材等比放进去。
    """

    def __init__(self, prs, index):
        slides = list(prs.slides)
        self.ok = index < len(slides)
        self.title_el = self.style_el = None
        self.part = None
        self.rects = []
        if not self.ok:
            return

        slide = slides[index]
        self.layout = slide.slide_layout
        self.part = slide.part

        texts = []
        for shape in slide.shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                self.rects.append((shape.left, shape.top, shape.width, shape.height))
            elif shape.has_text_frame and shape.text_frame.text.strip():
                biggest = max(
                    (run.font.size.pt for para in shape.text_frame.paragraphs
                     for run in para.runs if run.font.size),
                    default=0,
                )
                texts.append((biggest, shape))

        self.rects.sort(key=lambda r: r[0])          # 从左到右

        for _, shape in texts:
            if shape.text_frame.text.strip().lower().startswith("style"):
                self.style_el = shape._element
        # 标题 = 剩下的里字号最大的那个
        rest = [(sz, sh) for sz, sh in texts if sh._element is not self.style_el]
        if rest:
            self.title_el = max(rest, key=lambda item: item[0])[1]._element

        self.ok = bool(self.rects) and self.title_el is not None

    def describe(self):
        parts = [f"图片框 {len(self.rects)} 个"]
        for left, top, width, height in self.rects:
            parts.append(f"({Emu(left).inches:.2f}, {Emu(top).inches:.2f}) "
                         f"{Emu(width).inches:.2f}x{Emu(height).inches:.2f}in")
        parts.append("标题框 有" if self.title_el is not None else "标题框 无")
        parts.append("Style 框 有" if self.style_el is not None else "Style 框 无")
        return "　".join(parts)

    def overlap_warning(self):
        """模板上两个图片框可能相互重叠，满幅照片会互相遮挡。"""
        if len(self.rects) < 2:
            return None
        a, b = self.rects[0], self.rects[1]
        gap = b[0] - (a[0] + a[2])
        if gap >= 0:
            return None
        return (f"模板上两个图片框重叠 {Emu(-gap).inches:.2f} 英寸。"
                "白底服装图通常看不出来；若素材是满幅照片，右图会盖住左图，"
                "这时加 --side-by-side 让两框不重叠。")


def assign_fresh_ids(spTree, element):
    """给刚拷进来的形状换一批未被占用的 id。

    深拷贝会把源形状的 <p:cNvPr id> 一起带过来，而同一页里形状 id 必须唯一。
    撞了 id 的文件 PowerPoint 打开时会弹「内容有问题，是否修复」。
    """
    used = set()
    for node in spTree.iter(qn("p:cNvPr")):
        if node is element or element in node.iterancestors():
            continue
        raw = node.get("id")
        if raw and raw.isdigit():
            used.add(int(raw))

    next_id = 2                       # id=1 归 spTree 自己
    for node in element.iter(qn("p:cNvPr")):
        while next_id in used:
            next_id += 1
        node.set("id", str(next_id))
        used.add(next_id)


R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def remap_relationships(element, source_part, target_part):
    """把拷贝来的形状里的 r:id 重新指到目标页自己的关系上。

    深拷贝会把 r:id="rId2" 这类引用一起带过来，但那是**源页**的编号；新页上
    没有对应关系，就成了悬空引用，PowerPoint 打开时要求修复。能在源页解析到
    的就在目标页重建同样的关系并换成新编号；解析不到的（源页自己就是坏的）
    只能把这个属性去掉，宁可少个超链接也不要一份打不开的文件。
    """
    for node in element.iter():
        if not isinstance(node.tag, str):
            continue
        for key in list(node.attrib):
            if not key.startswith(R_NS):
                continue
            old_id = node.get(key)
            try:
                rel = source_part.rels[old_id]
            except KeyError:
                del node.attrib[key]
                continue
            if rel.is_external:
                new_id = target_part.relate_to(rel.target_ref, rel.reltype,
                                               is_external=True)
            else:
                new_id = target_part.relate_to(rel.target_part, rel.reltype)
            node.set(key, new_id)


def clone_textbox(slide, element, text, source_part=None):
    """深拷贝一个文本框并替换文字，格式原样保留。"""
    new_el = copy.deepcopy(element)
    spTree = slide.shapes._spTree
    spTree.append(new_el)
    assign_fresh_ids(spTree, new_el)
    if source_part is not None:
        remap_relationships(new_el, source_part, slide.part)

    for shape in slide.shapes:
        if shape._element is not new_el:
            continue
        frame = shape.text_frame
        first = None
        for para in list(frame.paragraphs):
            for run in list(para.runs):
                if first is None:
                    first = run          # 留住第一个 run，它带着格式
                else:
                    run._r.getparent().remove(run._r)
        if first is not None:
            first.text = text
        else:
            frame.text = text
        return shape
    return None


# --------------------------------------------------------------------------
# 建页
# --------------------------------------------------------------------------


class DeckBuilder:
    """所有生成页的文案一律英文；字体与正文字号取自模板的参考页。"""

    def __init__(self, prs, layouts, font, body_size=None, title_size=None,
                 reference=None, side_by_side=False):
        self.prs = prs
        self.layouts = layouts
        self.font = font
        self.body = body_size or Pt(14)
        self.title_size = title_size or Pt(20)
        self.caption = Pt(max(7.5, self.body.pt * 0.8))
        self.geo = Geometry(prs, layouts)
        self.reference = reference
        self.side_by_side = side_by_side
        self.missing = []

    def _slide(self, key):
        return self.prs.slides.add_slide(self.prs.slide_layouts[self.layouts[key]])

    # -- 节标题 -------------------------------------------------------------
    def section(self, title, note=""):
        slide = self._slide("section")
        fill_placeholder(slide, 0, [title], self.font, self.title_size)
        if note:
            fill_placeholder(slide, 1, [note], self.font, self.body)
        return slide

    # -- Brief Recap --------------------------------------------------------
    def brief_recap(self, brief, meta):
        """模板首页原样保留、不写入，所以项目信息放在这一页顶部。"""
        slide = self._slide("title_content")
        fill_placeholder(slide, 0, ["Brief Recap"], self.font, self.title_size)

        lines = []
        header = " · ".join(
            str(meta[k]) for k in ("project", "client", "req_id", "date") if meta.get(k)
        )
        if header:
            lines.append(header)
        lines += [f"{k}: {v}" for k, v in brief.items()]
        fill_placeholder(slide, 1, lines, self.font, self.body)
        return slide

    # -- 图片网格（灵感页 / 方案页 / 细节页共用）-----------------------------
    def image_grid(self, title, items, per_page=6, max_cols=3, lead=None):
        """items: [{image, caption}]，超过 per_page 自动分页。"""
        pages = [items[i : i + per_page] for i in range(0, len(items), per_page)] or [[]]
        slides = []

        for page_no, chunk in enumerate(pages, 1):
            slide = self._slide("title_only")
            heading = title if len(pages) == 1 else f"{title} ({page_no}/{len(pages)})"
            fill_placeholder(slide, 0, [heading], self.font, self.title_size)

            box = self.geo.title_only_body()
            if lead and page_no == 1:
                left, top, width, height = box
                lead_h = Inches(0.42)
                textbox(slide, (left, top, width, lead_h), [lead], font=self.font, size=self.body)
                box = (left, top + lead_h, width, height - lead_h)

            for cell, item in zip(grid_cells(box, len(chunk), max_cols=max_cols), chunk):
                path = Path(item["image"])
                if not path.exists():
                    # 页面上留红字占位，同时报到 stderr —— 几十张图时，
                    # 在命令行看一份缺失清单远比翻 PPT 找红块快。
                    self.missing.append(str(path))
                    textbox(slide, cell, [f"[Missing] {path.name}"], font=self.font, size=self.caption,
                            color=RGBColor(0xC0, 0x39, 0x2B))
                    continue
                place_contained(slide, path, cell, item.get("caption"), self.font, self.caption)
            slides.append(slide)
        return slides

    # -- 风格解构（色卡 + 要素）---------------------------------------------
    def decode(self, palette, elements):
        slide = self._slide("title_only")
        fill_placeholder(slide, 0, ["Style Breakdown"], self.font, self.title_size)

        left, top, width, height = self.geo.title_only_body()
        swatch_h = Inches(1.55)

        # 色卡：模板没有对应版式，用形状画。
        if palette:
            gap = Inches(0.14)
            cols = len(palette)
            cell_w = (width - gap * (cols - 1)) // cols
            chip_h = swatch_h - Inches(0.62)

            for i, color in enumerate(palette):
                x = left + i * (cell_w + gap)
                shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, top, cell_w, chip_h)
                shape.fill.solid()
                shape.fill.fore_color.rgb = RGBColor.from_string(color["hex"].lstrip("#").upper())
                shape.line.fill.background()
                shape.shadow.inherit = False
                shape.text_frame.text = ""

                textbox(
                    slide,
                    (x, top + chip_h + Inches(0.04), cell_w, Inches(0.55)),
                    [(color.get("name", ""), True), color.get("tcx", ""), color["hex"].upper()],
                    font=self.font,
                    size=self.caption,
                    align=PP_ALIGN.CENTER,
                )

        # 款式要素：两列铺开，避免一长条挤在左边。
        items = list(elements.items())
        if items:
            e_top = top + swatch_h + Inches(0.2)
            e_height = height - swatch_h - Inches(0.2)
            col_w = (width - Inches(0.3)) // 2
            half = math.ceil(len(items) / 2)

            for col, part in enumerate((items[:half], items[half:])):
                if not part:
                    continue
                textbox(
                    slide,
                    (left + col * (col_w + Inches(0.3)), e_top, col_w, e_height),
                    [f"{k}   {v}" for k, v in part],
                    font=self.font,
                    size=self.body,
                )
        return slide

    # -- 方案 ---------------------------------------------------------------
    def option(self, option):
        """一个方案 = 一组图片页。

        方案名当页标题、摘要当引导语，不再单独占一个节标题页 —— 否则
        「设计方案」分节页后面会紧跟一串节标题页，全是空版面。
        """
        lead = option.get("summary", "")
        notes = "; ".join(option.get("notes", []))
        if notes:
            lead = f"{lead}  ({notes})" if lead else notes

        self.image_grid(
            option.get("name", "Option"),
            option.get("images", []),
            per_page=option.get("per_page", 4),
            max_cols=2,
            lead=lead or None,
        )

    # -- 推荐页（一款一页，正反两图）-----------------------------------------
    def recommendation(self, item):
        """复刻模板参考页：两个文本框整段拷贝，两张图放进模板记下的矩形。"""
        ref = self.reference
        slide = self.prs.slides.add_slide(ref.layout)
        # add_slide 会按版式生成空占位符，参考页的内容是自带的，清掉免得重叠。
        for shape in list(slide.shapes):
            shape._element.getparent().remove(shape._element)

        rects = list(ref.rects)
        if self.side_by_side and len(rects) >= 2:
            rects = self._split_evenly(rects)

        for rect, key in zip(rects, ("front", "back")):
            path = item.get(key)
            if not path:
                continue
            path = Path(path)
            if not path.exists():
                self.missing.append(str(path))
                textbox(slide, rect, [f"[Missing] {path.name}"], font=self.font,
                        size=self.caption, color=RGBColor(0xC0, 0x39, 0x2B))
                continue
            place_contained(slide, path, rect)

        if ref.title_el is not None:
            clone_textbox(slide, ref.title_el, item.get("title", "Recommendation"), ref.part)
        if ref.style_el is not None:
            clone_textbox(slide, ref.style_el,
                          f"Style: {item.get('style', '')}".rstrip(), ref.part)

        notes = item.get("notes") or []
        if notes and ref.style_el is not None:
            # 备注放在 Style 框右侧同一条带上，不动模板原有的框。
            style_shape = [sh for sh in slide.shapes
                           if sh.has_text_frame
                           and sh.text_frame.text.strip().lower().startswith("style")]
            if style_shape:
                sh = style_shape[-1]
                left = sh.left + sh.width + Inches(0.2)
                width = self.prs.slide_width - left - Inches(0.5)
                if width > Inches(1):
                    textbox(slide, (left, sh.top, width, sh.height),
                            ["   ".join(notes)], font=self.font, size=self.body)
        return slide

    @staticmethod
    def _split_evenly(rects):
        """把重叠的两个图片框改成左右均分且不重叠，保留原有的上下范围。"""
        left = min(r[0] for r in rects)
        right = max(r[0] + r[2] for r in rects)
        top = min(r[1] for r in rects)
        bottom = max(r[1] + r[3] for r in rects)
        gap = Inches(0.2)
        width = (right - left - gap) // 2
        height = bottom - top
        return [(left, top, width, height), (left + width + gap, top, width, height)]

    # -- 素材来源与授权 -----------------------------------------------------
    def sources(self, rows):
        slide = self._slide("title_only")
        fill_placeholder(slide, 0, ["Sources & Licensing"], self.font, self.title_size)

        left, top, width, height = self.geo.title_only_body()
        headers = ["No.", "Brand", "Season", "Source", "Usage"]
        keys = ["no", "brand", "season", "url", "usage"]

        table_h = min(height, Inches(0.32) * (len(rows) + 1))
        table = slide.shapes.add_table(len(rows) + 1, len(headers), left, top, width, table_h).table
        for c, head in enumerate(headers):
            cell = table.cell(0, c)
            cell.text = head
            style_run(cell.text_frame.paragraphs[0].runs[0], font=self.font, size=self.caption, bold=True)

        for r, row in enumerate(rows, start=1):
            for c, key in enumerate(keys):
                cell = table.cell(r, c)
                cell.text = str(row.get(key, ""))
                style_run(cell.text_frame.paragraphs[0].runs[0], font=self.font, size=self.caption)

        textbox(
            slide,
            (left, top + table_h + Inches(0.12), width, Inches(0.5)),
            ['Items marked "Reference only" are inspiration sources. They are not '
             "deliverables and must not be presented as original work."],
            font=self.font,
            size=self.caption,
            color=RGBColor(0xA8, 0x3A, 0x2C),
        )
        return slide


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------


def build(brief, template, output, layouts, keep_ends=True, ref_page=1,
          font_override=None, body_size=None, title_size=20.0, side_by_side=False):
    print(f"build_deck 版本 {VERSION}")
    prs = open_template(template)

    # 采样必须在剪页之前 —— 参考页本身就是要被剪掉的那批。
    typo = sample_typography(prs, ref_page)
    font = font_override or typo["font"] or theme_font(prs)
    size = Pt(body_size) if body_size else typo["size"]

    print(f"参考页：{typo['source']}")
    for name, sz, text in typo["found"]:
        print(f"    {name or '(继承)':<18} {sz.pt if sz else '(继承)':>7}pt   {text}")
    print(f"采用 → 字体 {font or '(PowerPoint 默认)'}　"
          f"正文字号 {size.pt if size else 11}pt"
          f"{'（手动指定）' if (font_override or body_size) else '（正文取最小的一档；不对就用 --font / --body-size 覆盖）'}")

    # 先记下模板原有的页，内容页生成完之后再动它们。
    originals = list(prs.slides._sldIdLst)
    head = originals[0] if originals and keep_ends else None
    tail = originals[-1] if len(originals) > 1 and keep_ends else None

    reference = ReferencePage(prs, ref_page)
    if brief.get("recommendations"):
        if reference.ok:
            print(f"推荐页参考：{reference.describe()}")
            warning = reference.overlap_warning()
            if warning and not side_by_side:
                print(f"  提示：{warning}")
        else:
            sys.exit(f"第 {ref_page + 1} 页缺少可复刻的图片框或标题框，"
                     "无法生成推荐页。用 --ref-page 指定正确的样板页。")

    deck = DeckBuilder(prs, layouts, font, size, Pt(title_size), reference, side_by_side)

    if brief.get("brief") or brief.get("meta"):
        deck.brief_recap(brief.get("brief", {}), brief.get("meta", {}))

    for item in brief.get("recommendations", []):
        deck.recommendation(item)

    if brief.get("inspiration"):
        deck.section("Trend Inspiration",
                     "Reference material only — not part of the deliverable.")
        deck.image_grid("Trend Inspiration", brief["inspiration"], per_page=6, max_cols=3)

    decode = brief.get("decode") or {}
    if decode.get("palette") or decode.get("elements"):
        deck.decode(decode.get("palette", []), decode.get("elements", {}))

    if brief.get("options"):
        deck.section("Design Options")
        for option in brief["options"]:
            deck.option(option)

    if brief.get("details"):
        deck.image_grid("Detail Views", brief["details"], per_page=4, max_cols=2)

    if brief.get("sources"):
        deck.sources(brief["sources"])

    # 内容页已经生成完，现在才删模板中间的示例页，避免部件名撞车。
    drop_slides(prs, [sid for sid in originals if sid is not head and sid is not tail])

    # 新页只会追加到末尾，所以尾页要挪回去。
    if tail is not None:
        move_to_end(prs, tail)

    if deck.missing:
        print(f"\n找不到 {len(deck.missing)} 张图（页面上已用红字占位）：", file=sys.stderr)
        for path in deck.missing:
            print(f"  - {path}", file=sys.stderr)
        print("  路径是相对于你运行命令的位置，不是相对 brief.json。\n", file=sys.stderr)

    prs.save(output)
    return len(prs.slides)


SAMPLE = {
    "meta": {
        "project": "Y2K Bomber Jacket — Design Proposal",
        "client": "Client A",
        "req_id": "REQ-2026-0913",
        "date": "2026-09-13",
        "designer": "Sheji",
    },
    "brief": {
        "Category": "Women's bomber jacket",
        "Silhouette": "Oversized, dropped shoulder",
        "Target": "Ages 18-25",
        "Price band": "RMB 299-459",
        "Season": "AW26",
        "Colour direction": "Cool metallics with one high-saturation accent",
        "Fabric": "Polyester taffeta, 80-110 gsm",
        "Excluded": "No rhinestones, no large-area embroidery",
        "Due": "2026-09-13",
    },
    "recommendations": [
        {"style": "001", "title": "Knitwear Recommendation",
         "front": "img/001-front.jpg", "back": "img/001-back.jpg",
         "notes": ["Crew neck, ribbed hem", "12gg fully fashioned"]},
        {"style": "002", "title": "Knitwear Recommendation",
         "front": "img/002-front.jpg", "back": "img/002-back.jpg",
         "notes": ["Half zip, raglan sleeve"]},
    ],
    "inspiration": [
        {"image": "img/A01.jpg", "caption": "A01 · Brand X · AW26"},
        {"image": "img/A02.jpg", "caption": "A02 · Brand X · AW26"},
        {"image": "img/A03.jpg", "caption": "A03 · Pinterest"},
    ],
    "decode": {
        "palette": [
            {"name": "Titanium", "hex": "#B9BDC2", "tcx": "14-4102 TCX"},
            {"name": "Deep Navy", "hex": "#1F3A5F", "tcx": "19-4027 TCX"},
            {"name": "Cold Mist", "hex": "#8A9299", "tcx": "16-3915 TCX"},
            {"name": "Signal Orange", "hex": "#D9541E", "tcx": "17-1464 TCX"},
        ],
        "elements": {
            "Silhouette": "Oversized, shoulder dropped 4-5 cm",
            "Collar": "Stand collar, 6 cm height",
            "Sleeve": "Dropped raglan, ribbed cuff",
            "Front": "Two-way zip with metal pull tab",
            "Seams": "Horizontal front yoke",
            "Pockets": "Slant pockets, left chest patch",
            "Hem": "Drawcord",
            "Fabric hand": "Taffeta, matte metallic sheen",
        },
    },
    "options": [
        {
            "name": "Option A — Cold Utility",
            "summary": "Metallic hand and structural seaming; titanium over deep navy.",
            "notes": ["Primary 14-4102 TCX", "Zip hardware is the only accent"],
            "images": [
                {"image": "img/G-A01.png", "caption": "G-A01"},
                {"image": "img/G-A02.png", "caption": "G-A02"},
            ],
        },
        {
            "name": "Option B — Saturated Block",
            "summary": "Signal orange as a colour block; seaming played down.",
            "notes": ["Accent kept under 15% of surface"],
            "images": [{"image": "img/G-B01.png", "caption": "G-B01"}],
        },
    ],
    "details": [
        {"image": "img/D01.jpg", "caption": "Collar and zip pull"},
        {"image": "img/D02.jpg", "caption": "Hem drawcord"},
    ],
    "sources": [
        {"no": "A01", "brand": "Brand X", "season": "AW26",
         "url": "brand-x.com/lookbook", "usage": "Reference only"},
        {"no": "A03", "brand": "—", "season": "—",
         "url": "pinterest.com/pin/...", "usage": "Reference only"},
        {"no": "G-A01", "brand": "Firefly (generated)", "season": "—",
         "url": "local", "usage": "Deliverable"},
    ],
}


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp"}


def scan_images(folder, pair, out=None, into=None):
    """扫描文件夹，输出可直接粘进 brief.json 的 recommendations 骨架。

    服装图的文件名常带空格、括号和加号，手抄进 JSON 很容易打错，让脚本
    照着磁盘上的实际文件名生成。
    """
    root = Path(folder)
    if not root.is_dir():
        sys.exit(f"找不到文件夹：{folder}")

    files = sorted(
        (p for p in root.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES),
        key=lambda p: p.name.lower(),
    )
    if not files:
        sys.exit(f"{folder} 里没有找到图片")

    def rel(path):
        # brief.json 里的路径是相对运行命令的位置；扫当前目录时去掉 "./"
        text = path.as_posix()
        return text[2:] if text.startswith("./") else text

    items = []
    if pair:
        for i in range(0, len(files), 2):
            entry = {
                "style": f"{i // 2 + 1:03d}",
                "title": "Knitwear Recommendation",
                "front": rel(files[i]),
                "back": rel(files[i + 1]) if i + 1 < len(files) else "",
                "notes": [],
            }
            items.append(entry)
    else:
        for i, path in enumerate(files, 1):
            items.append({
                "style": f"{i:03d}",
                "title": "Knitwear Recommendation",
                "front": rel(path),
                "back": "",
                "notes": [],
            })

    print(f"扫到 {len(files)} 张图 → {len(items)} 页推荐页"
          f"（{'正反配对' if pair else '一图一页'}）", file=sys.stderr)

    if into:
        # 直接改 brief.json：复制粘贴那一步最容易出错，索性省掉。
        # 只换 recommendations，其余字段原样保留。
        path = Path(into)
        brief = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
        brief["recommendations"] = items
        path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {into} 的 recommendations（其余字段保留）", file=sys.stderr)
        return

    text = json.dumps({"recommendations": items}, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print(f"已写出 {out}", file=sys.stderr)
    else:
        print(text)


def main():
    parser = argparse.ArgumentParser(description="按模板生成设计提案 PPT")
    parser.add_argument("brief", nargs="?", help="内容 JSON")
    parser.add_argument("-t", "--template", help=".potx / .pptx 模板")
    parser.add_argument("-o", "--output", default="提案.pptx", help="输出文件名")
    parser.add_argument("--drop-ends", action="store_true",
                        help="连模板首页和尾页一起删掉（默认保留、原样不动）")
    parser.add_argument("--ref-page", type=int, default=2,
                        help="用模板第几页作为字体字号的采样源（默认 2）")
    parser.add_argument("--font", help="直接指定字体，覆盖采样结果")
    parser.add_argument("--body-size", type=float, default=14.0,
                        help="正文字号（pt），默认 14")
    parser.add_argument("--title-size", type=float, default=20.0,
                        help="生成页的标题字号（pt），默认 20；推荐页照抄模板，不受影响")
    parser.add_argument("--side-by-side", action="store_true",
                        help="推荐页两张图改为左右均分不重叠（模板上两框是重叠的）")
    parser.add_argument("--layout-map", help='版式索引覆盖，如 \'{"section": 4}\'')
    parser.add_argument("--sample-brief", action="store_true", help="打印示例 JSON 后退出")
    parser.add_argument("--scan-images", metavar="DIR",
                        help="扫描文件夹里的图片，生成 recommendations 骨架后退出")
    parser.add_argument("--pair", action="store_true",
                        help="配合 --scan-images：两张一组当正反面（默认一图一页）")
    parser.add_argument("--into", metavar="BRIEF",
                        help="配合 --scan-images：直接改写 brief.json 的 recommendations")
    args = parser.parse_args()
    args.output_or_none = args.output if args.output != "提案.pptx" else None

    if args.scan_images:
        print(f"build_deck 版本 {VERSION}", file=sys.stderr)
        scan_images(args.scan_images, args.pair, out=args.output_or_none, into=args.into)
        return

    if args.sample_brief:
        text = json.dumps(SAMPLE, ensure_ascii=False, indent=2)
        if args.output and args.output != "提案.pptx":
            # 让 Python 自己写文件：PowerShell 的 > 会写成 UTF-16，
            # Out-File -Encoding utf8 又带 BOM，两者都让 JSON 变得读不了。
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"已写出示例 brief：{args.output}")
        else:
            print(text)
        return

    if not args.brief or not args.template:
        parser.error("需要 brief JSON 和 --template")

    layouts = dict(LAYOUTS)
    if args.layout_map:
        layouts.update(json.loads(args.layout_map))

    # utf-8-sig 而不是 utf-8：Windows 的编辑器和 PowerShell 重定向常带 BOM，
    # 用 utf-8 读会以 "Expecting value: line 1 column 1" 失败，看不出真正原因。
    brief = json.loads(Path(args.brief).read_text(encoding="utf-8-sig"))
    n = build(brief, args.template, args.output, layouts,
              keep_ends=not args.drop_ends, ref_page=args.ref_page - 1,
              font_override=args.font, body_size=args.body_size,
              title_size=args.title_size, side_by_side=args.side_by_side)
    print(f"已生成 {args.output}（{n} 页）")


if __name__ == "__main__":
    main()
