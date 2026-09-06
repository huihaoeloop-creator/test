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


def drop_all_slides(prs):
    """清空模板自带的示例页。python-pptx 没有公开的删页 API，只能动 XML。"""
    id_list = prs.slides._sldIdLst
    for slide_id in list(id_list):
        prs.part.drop_rel(slide_id.get(qn("r:id")))
        id_list.remove(slide_id)


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


def fill_placeholder(slide, idx, lines, font=None):
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
        if font:
            style_run(run, font=font, size=run.font.size)
    return ph


# --------------------------------------------------------------------------
# 图片
# --------------------------------------------------------------------------


def place_contained(slide, image_path, cell, caption=None, font=None):
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
            size=Pt(9),
            color=RGBColor(0x80, 0x80, 0x80),
            align=PP_ALIGN.CENTER,
        )
    return pic


# --------------------------------------------------------------------------
# 建页
# --------------------------------------------------------------------------


class DeckBuilder:
    def __init__(self, prs, layouts, font):
        self.prs = prs
        self.layouts = layouts
        self.font = font
        self.geo = Geometry(prs, layouts)

    def _slide(self, key):
        return self.prs.slides.add_slide(self.prs.slide_layouts[self.layouts[key]])

    # -- 封面 ---------------------------------------------------------------
    def cover(self, meta):
        slide = self._slide("cover")
        fill_placeholder(slide, 0, [meta.get("project", "设计提案")], self.font)
        subtitle = " · ".join(
            str(meta[k]) for k in ("client", "req_id", "date", "designer") if meta.get(k)
        )
        fill_placeholder(slide, 1, [subtitle], self.font)
        return slide

    # -- 节标题 -------------------------------------------------------------
    def section(self, title, note=""):
        slide = self._slide("section")
        fill_placeholder(slide, 0, [title], self.font)
        if note:
            fill_placeholder(slide, 1, [note], self.font)
        return slide

    # -- 需求回顾 -----------------------------------------------------------
    def brief_recap(self, brief):
        slide = self._slide("title_content")
        fill_placeholder(slide, 0, ["需求回顾"], self.font)
        fill_placeholder(
            slide, 1, [f"{k}：{v}" for k, v in brief.items()], self.font
        )
        return slide

    # -- 图片网格（灵感页 / 方案页 / 细节页共用）-----------------------------
    def image_grid(self, title, items, per_page=6, max_cols=3, lead=None):
        """items: [{image, caption}]，超过 per_page 自动分页。"""
        pages = [items[i : i + per_page] for i in range(0, len(items), per_page)] or [[]]
        slides = []

        for page_no, chunk in enumerate(pages, 1):
            slide = self._slide("title_only")
            heading = title if len(pages) == 1 else f"{title}（{page_no}/{len(pages)}）"
            fill_placeholder(slide, 0, [heading], self.font)

            box = self.geo.title_only_body()
            if lead and page_no == 1:
                left, top, width, height = box
                lead_h = Inches(0.42)
                textbox(slide, (left, top, width, lead_h), [lead], font=self.font, size=Pt(12))
                box = (left, top + lead_h, width, height - lead_h)

            for cell, item in zip(grid_cells(box, len(chunk), max_cols=max_cols), chunk):
                path = Path(item["image"])
                if not path.exists():
                    textbox(slide, cell, [f"[缺图] {path.name}"], font=self.font, size=Pt(10),
                            color=RGBColor(0xC0, 0x39, 0x2B))
                    continue
                place_contained(slide, path, cell, item.get("caption"), self.font)
            slides.append(slide)
        return slides

    # -- 风格解构（色卡 + 要素）---------------------------------------------
    def decode(self, palette, elements):
        slide = self._slide("title_only")
        fill_placeholder(slide, 0, ["风格解构"], self.font)

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
                    size=Pt(9),
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
                    [f"{k}　{v}" for k, v in part],
                    font=self.font,
                    size=Pt(11.5),
                )
        return slide

    # -- 方案 ---------------------------------------------------------------
    def option(self, option):
        """一个方案 = 一组图片页。

        方案名当页标题、摘要当引导语，不再单独占一个节标题页 —— 否则
        「设计方案」分节页后面会紧跟一串节标题页，全是空版面。
        """
        lead = option.get("summary", "")
        notes = "；".join(option.get("notes", []))
        if notes:
            lead = f"{lead}　（{notes}）" if lead else notes

        self.image_grid(
            option.get("name", "方案"),
            option.get("images", []),
            per_page=option.get("per_page", 4),
            max_cols=2,
            lead=lead or None,
        )

    # -- 素材来源与授权 -----------------------------------------------------
    def sources(self, rows):
        slide = self._slide("title_only")
        fill_placeholder(slide, 0, ["素材来源与授权说明"], self.font)

        left, top, width, height = self.geo.title_only_body()
        headers = ["编号", "品牌", "季度", "来源", "用途"]
        keys = ["no", "brand", "season", "url", "usage"]

        table_h = min(height, Inches(0.32) * (len(rows) + 1))
        table = slide.shapes.add_table(len(rows) + 1, len(headers), left, top, width, table_h).table
        for c, head in enumerate(headers):
            cell = table.cell(0, c)
            cell.text = head
            style_run(cell.text_frame.paragraphs[0].runs[0], font=self.font, size=Pt(10), bold=True)

        for r, row in enumerate(rows, start=1):
            for c, key in enumerate(keys):
                cell = table.cell(r, c)
                cell.text = str(row.get(key, ""))
                style_run(cell.text_frame.paragraphs[0].runs[0], font=self.font, size=Pt(9))

        textbox(
            slide,
            (left, top + table_h + Inches(0.12), width, Inches(0.5)),
            ["标注「仅参考」的素材为灵感来源，不构成交付内容，不得作为原创方案使用。"],
            font=self.font,
            size=Pt(9),
            color=RGBColor(0xA8, 0x3A, 0x2C),
        )
        return slide


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------


def build(brief, template, output, layouts, keep_template_slides=False):
    prs = open_template(template)
    if not keep_template_slides:
        drop_all_slides(prs)

    font = theme_font(prs)
    deck = DeckBuilder(prs, layouts, font)

    deck.cover(brief.get("meta", {}))
    if brief.get("brief"):
        deck.brief_recap(brief["brief"])

    if brief.get("inspiration"):
        deck.section("趋势灵感", "以下为参考素材，仅作方向说明，不构成交付内容。")
        deck.image_grid("趋势灵感", brief["inspiration"], per_page=6, max_cols=3)

    decode = brief.get("decode") or {}
    if decode.get("palette") or decode.get("elements"):
        deck.decode(decode.get("palette", []), decode.get("elements", {}))

    if brief.get("options"):
        deck.section("设计方案")
        for option in brief["options"]:
            deck.option(option)

    if brief.get("details"):
        deck.image_grid("细节放大", brief["details"], per_page=4, max_cols=2)

    if brief.get("sources"):
        deck.sources(brief["sources"])

    prs.save(output)
    return len(prs.slides)


SAMPLE = {
    "meta": {
        "project": "Y2K 女装夹克 · 设计提案",
        "client": "客户 A",
        "req_id": "REQ-2026-0913",
        "date": "2026-09-06",
        "designer": "佘吉",
    },
    "brief": {
        "品类": "女装夹克",
        "廓形": "Oversized 落肩",
        "目标客群": "18-25 岁",
        "价格带": "¥299-459",
        "季节": "2026 秋冬",
        "色彩倾向": "冷调金属感 + 一处高饱和",
        "面料": "涤纶塔夫绸 / 面密度 80-110 gsm",
        "工艺禁忌": "不接受烫钻、不接受大面积刺绣",
        "交付日": "2026-09-13",
    },
    "inspiration": [
        {"image": "fixtures/img/ref-01.png", "caption": "A01 · 品牌官网 · 26AW"},
        {"image": "fixtures/img/ref-02.png", "caption": "A02 · 品牌官网 · 26AW"},
        {"image": "fixtures/img/ref-03.png", "caption": "A03 · Pinterest"},
        {"image": "fixtures/img/ref-04.png", "caption": "A04 · Pinterest"},
        {"image": "fixtures/img/ref-05.png", "caption": "A05 · 趋势报告"},
        {"image": "fixtures/img/ref-06.png", "caption": "A06 · 趋势报告"},
    ],
    "decode": {
        "palette": [
            {"name": "钛银", "hex": "#B9BDC2", "tcx": "14-4102 TCX"},
            {"name": "深海军", "hex": "#1F3A5F", "tcx": "19-4027 TCX"},
            {"name": "冷雾灰", "hex": "#8A9299", "tcx": "16-3915 TCX"},
            {"name": "信号橙", "hex": "#D9541E", "tcx": "17-1464 TCX"},
            {"name": "近黑", "hex": "#16181C", "tcx": "19-4205 TCX"},
        ],
        "elements": {
            "廓形": "Oversized，肩线下落 4-5 cm",
            "领型": "立领，领高 6 cm",
            "袖型": "落肩插肩袖，袖口罗纹",
            "门襟": "双向拉链，拉头加金属挂片",
            "分割线": "前身横向育克分割",
            "口袋": "斜插袋 + 左胸贴袋",
            "下摆": "抽绳收口",
            "面料肌理": "塔夫绸，哑光金属反射",
        },
    },
    "options": [
        {
            "name": "方案 A · 机能冷调",
            "summary": "强调金属感面料与结构分割，主色钛银配深海军。",
            "notes": ["主色 14-4102 TCX", "拉链与挂片为唯一亮点"],
            "images": [
                {"image": "fixtures/img/gen-a1.png", "caption": "G-A01"},
                {"image": "fixtures/img/gen-a2.png", "caption": "G-A02"},
            ],
        },
        {
            "name": "方案 B · 高饱和撞色",
            "summary": "以信号橙作块面撞色，弱化分割线。",
            "notes": ["撞色面积控制在 15% 以内"],
            "images": [
                {"image": "fixtures/img/gen-b1.png", "caption": "G-B01"},
                {"image": "fixtures/img/gen-b2.png", "caption": "G-B02"},
            ],
        },
    ],
    "details": [
        {"image": "fixtures/img/det-01.png", "caption": "领口与拉链头"},
        {"image": "fixtures/img/det-02.png", "caption": "下摆抽绳"},
    ],
    "sources": [
        {"no": "A01", "brand": "品牌 X", "season": "26AW", "url": "brand-x.com/lookbook", "usage": "仅参考"},
        {"no": "A03", "brand": "—", "season": "—", "url": "pinterest.com/pin/…", "usage": "仅参考"},
        {"no": "G-A01", "brand": "Firefly 生成", "season": "—", "url": "本地", "usage": "可交付"},
    ],
}


def main():
    parser = argparse.ArgumentParser(description="按模板生成设计提案 PPT")
    parser.add_argument("brief", nargs="?", help="内容 JSON")
    parser.add_argument("-t", "--template", help=".potx / .pptx 模板")
    parser.add_argument("-o", "--output", default="提案.pptx", help="输出文件名")
    parser.add_argument("--keep-template-slides", action="store_true",
                        help="保留模板自带的幻灯片（默认清空）")
    parser.add_argument("--layout-map", help='版式索引覆盖，如 \'{"section": 4}\'')
    parser.add_argument("--sample-brief", action="store_true", help="打印示例 JSON 后退出")
    args = parser.parse_args()

    if args.sample_brief:
        print(json.dumps(SAMPLE, ensure_ascii=False, indent=2))
        return

    if not args.brief or not args.template:
        parser.error("需要 brief JSON 和 --template")

    layouts = dict(LAYOUTS)
    if args.layout_map:
        layouts.update(json.loads(args.layout_map))

    brief = json.loads(Path(args.brief).read_text(encoding="utf-8"))
    n = build(brief, args.template, args.output, layouts, args.keep_template_slides)
    print(f"已生成 {args.output}（{n} 页）")


if __name__ == "__main__":
    main()
