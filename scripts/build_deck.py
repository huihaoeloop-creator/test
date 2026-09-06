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

绝不新建版式、绝不改母版 —— 只用现成版式和自己画的形状。

用法：
    pip install python-pptx
    python -X utf8 build_deck.py brief.json -t 模板.potx -o 提案.pptx
    python -X utf8 build_deck.py --sample-brief > brief.json   # 生成示例输入
"""
from __future__ import annotations

import argparse
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
    from pptx.enum.shapes import MSO_SHAPE
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
# 建页
# --------------------------------------------------------------------------


class DeckBuilder:
    """所有生成页的文案一律英文；字体与正文字号取自模板的参考页。"""

    def __init__(self, prs, layouts, font, body_size=None):
        self.prs = prs
        self.layouts = layouts
        self.font = font
        # 采样不到就退回 11pt —— 保证还能出稿，命令行会说明退回了。
        self.body = body_size or Pt(11)
        self.caption = Pt(max(7.5, self.body.pt * 0.8))
        self.geo = Geometry(prs, layouts)
        self.missing = []

    def _slide(self, key):
        return self.prs.slides.add_slide(self.prs.slide_layouts[self.layouts[key]])

    # -- 节标题 -------------------------------------------------------------
    def section(self, title, note=""):
        slide = self._slide("section")
        fill_placeholder(slide, 0, [title], self.font)
        if note:
            fill_placeholder(slide, 1, [note], self.font, self.body)
        return slide

    # -- Brief Recap --------------------------------------------------------
    def brief_recap(self, brief, meta):
        """模板首页原样保留、不写入，所以项目信息放在这一页顶部。"""
        slide = self._slide("title_content")
        fill_placeholder(slide, 0, ["Brief Recap"], self.font)

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
            fill_placeholder(slide, 0, [heading], self.font)

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
        fill_placeholder(slide, 0, ["Style Breakdown"], self.font)

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

    # -- 素材来源与授权 -----------------------------------------------------
    def sources(self, rows):
        slide = self._slide("title_only")
        fill_placeholder(slide, 0, ["Sources & Licensing"], self.font)

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
          font_override=None, body_size=None):
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

    deck = DeckBuilder(prs, layouts, font, size)

    if brief.get("brief") or brief.get("meta"):
        deck.brief_recap(brief.get("brief", {}), brief.get("meta", {}))

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
    parser.add_argument("--body-size", type=float, help="直接指定正文字号（pt），覆盖采样结果")
    parser.add_argument("--layout-map", help='版式索引覆盖，如 \'{"section": 4}\'')
    parser.add_argument("--sample-brief", action="store_true", help="打印示例 JSON 后退出")
    args = parser.parse_args()

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
              font_override=args.font, body_size=args.body_size)
    print(f"已生成 {args.output}（{n} 页）")


if __name__ == "__main__":
    main()
