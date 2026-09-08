#!/usr/bin/env python3
"""
节点 01 · 检索策略。

把签过字的需求单（节点 00 的 intake.json）变成一份可执行的检索方案：
中英关键词矩阵、按渠道分配的图量配额、以及每个渠道的合规边界。

这一步的价值有两条：

1. 翻译。陈主管的需求是中文，要搜的站基本是英文站。这里把词translate
   一次，翻不出来的**不猜**，列进「需补英文」交回去问 —— 一个词翻错，
   后面几十张图全跑偏。
2. 合规。哪些渠道佘吉能自己跑、哪些只能出清单让有账号的人导，写死在
   CHANNELS 表里，而不是靠跑的时候临场判断。

用法：
    python -X utf8 search_plan.py intake.json --target 40
    python -X utf8 search_plan.py intake.json --target 40 -o plan.json
    python -X utf8 search_plan.py intake.json --queries      # 只打关键词
    python -X utf8 search_plan.py --lexicon                  # 看词表
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VERSION = "2026-09-08h"

# --------------------------------------------------------------------------
# 渠道表 —— 合规边界写在这里，不在代码逻辑里
# --------------------------------------------------------------------------
# mode:
#   auto      佘吉可以直接跑（浏览器自动化）
#   auto-api  只能走官方 API，不许抓网页
#   manual    佘吉只出选片清单，由有账号的人手动导出
#   off       不采集
CHANNELS = [
    {
        "key": "client",
        "name": "客户官网",
        "mode": "auto",
        "lang": "en",
        "weight": 0.18,
        "license": "内部参考",
        "note": "客户自己在售的款。用来摸清他们的版型习惯、克重和价位 —— "
                "这是判断新方向能不能落地的基准。仍然只能内部看，"
                "不得把客户现有款当成我们的提案放进交付页。",
    },
    {
        "key": "brand",
        "name": "对标品牌官网",
        "mode": "auto",
        "lang": "en",
        "weight": 0.14,
        "license": "内部参考",
        "note": "竞品在售的款，用来看市场。交付 PPT 必须保留 Sources 页并注明品牌，"
                "任何情况下不得当作原创款呈现给客户。",
    },
    {
        "key": "pinterest",
        "name": "Pinterest",
        "mode": "auto-api",
        "lang": "en",
        "weight": 0.18,
        "license": "官方 API",
        "note": "只走官方 API，保留 pin 链接与作者署名；不抓网页、不绕登录。",
    },
    {
        "key": "taobao",
        "name": "淘宝 / 天猫",
        "mode": "manual",
        "lang": "zh",
        "weight": 0.16,
        "license": "ToS 禁止抓取；商品图版权属卖家",
        "note": "看的是国内在卖什么、什么价、销量排序靠前的是哪些货型 —— "
                "市场与供应链信号，不是趋势权威。佘吉只出选品清单（关键词 + 筛选条件 + "
                "张数），人工在浏览器里选。公司若有淘宝开放平台/联盟 appkey，"
                "这条可以改成 auto-api，届时改 mode 即可。",
    },
    {
        "key": "xiaohongshu",
        "name": "小红书",
        "mode": "manual",
        "lang": "zh",
        "weight": 0.16,
        "license": "ToS 禁止抓取；UGC 版权属创作者",
        "note": "看的是消费者在穿什么、什么内容有互动 —— 需求端信号。"
                "笔记图是个人创作者的作品，内部参考尚可，"
                "**绝不能进任何交付物**，搬运和洗稿的法律风险是实的。"
                "佘吉只出选品清单，人工浏览。",
    },
    {
        "key": "wgsn",
        "name": "WGSN / Fashion Snoops",
        "mode": "manual",
        "lang": "en",
        "weight": 0.18,
        "license": "订阅制，ToS 禁止抓取",
        "note": "佘吉只产出选片清单（栏目路径 + 关键词 + 目标张数），"
                "由持账号的同事手动导出后放进 02 的素材目录。",
    },
    {
        "key": "instagram",
        "name": "Instagram",
        "mode": "off",
        "weight": 0.0,
        "license": "ToS 禁止抓取",
        "note": "不做任何自动采集。人工刷到的参考只记链接，不入素材库。",
    },
]

CHANNEL_BY_KEY = {c["key"]: c for c in CHANNELS}

# --------------------------------------------------------------------------
# 中英词表 —— 要加词直接改这里
# --------------------------------------------------------------------------
# 只收行业里有稳定英文对照的词。没把握的宁可不收，让它进「需补英文」，
# 也不要放一个似是而非的翻译进去。
LEXICON = {
    # 限定 —— 复合词的前半段，丢了检索面会大一个数量级
    "女装": "womenswear", "男装": "menswear", "童装": "kidswear",
    "女士": "women", "男士": "men",
    # 品类
    "针织衫": "knitwear", "毛衣": "sweater", "开衫": "cardigan",
    "夹克": "jacket", "外套": "outerwear", "大衣": "coat",
    "风衣": "trench coat", "羽绒服": "puffer jacket", "棉服": "padded jacket",
    "连衣裙": "dress", "半裙": "skirt", "衬衫": "shirt", "衬衣": "blouse",
    "卫衣": "hoodie", "T恤": "t-shirt", "西装": "tailored jacket",
    "马甲": "vest", "裤": "trousers", "牛仔裤": "jeans", "套装": "co-ord set",
    # 廓形
    "廓形": "silhouette", "落肩": "drop shoulder", "宽松": "relaxed fit",
    "修身": "slim fit", "直筒": "straight cut", "收腰": "cinched waist",
    "茧型": "cocoon shape", "A摆": "a-line", "H型": "column silhouette",
    "垫肩": "shoulder pad", "高领": "high neck", "翻领": "lapel collar",
    "圆领": "crew neck", "V领": "v-neck", "泡泡袖": "puff sleeve",
    "灯笼袖": "balloon sleeve", "插肩袖": "raglan sleeve",
    # 色彩
    "冷调": "cool tone", "暖调": "warm tone", "高饱和": "saturated",
    "低饱和": "desaturated", "莫兰迪": "muted tone", "大地色": "earth tone",
    "金属感": "metallic", "撞色": "colour blocking", "渐变": "ombre",
    "拼色": "panelled colour", "单色": "monochrome", "中性色": "neutral palette",
    # 面料 / 工艺
    "羊毛": "wool", "马海毛": "mohair", "羊绒": "cashmere", "涤纶": "polyester",
    "尼龙": "nylon", "棉": "cotton", "亚麻": "linen", "醋酸": "acetate",
    "塔夫绸": "taffeta", "灯芯绒": "corduroy", "牛仔": "denim",
    "提花": "jacquard", "罗纹": "rib knit", "绞花": "cable knit",
    "麻花": "cable knit", "抽针": "pointelle", "镂空": "open work",
    "刺绣": "embroidery", "印花": "print", "褶皱": "pleated",
    "美利奴": "merino", "细针": "fine gauge", "粗针": "chunky gauge",
    "半拉链": "half zip", "立领": "funnel neck", "菱格": "argyle",
    "嵌花": "intarsia", "条纹": "stripe", "同色系": "tonal",
    "圆领": "crew neck", "落肩袖": "drop shoulder", "茧型": "cocoon shape",
    "起球": "pilling", "垂坠": "drape",
    "绗缝": "quilted", "做旧": "washed finish", "毛边": "raw edge",
    # 客群 / 定位
    "通勤": "workwear", "街头": "streetwear", "学院": "preppy",
    "运动": "sportswear", "户外": "outdoor", "度假": "resort",
    "极简": "minimal", "复古": "vintage", "甜酷": "sweet cool",
}

SEASON = re.compile(r"(20\d{2})?\s*(秋冬|春夏|春季|夏季|秋季|冬季|AW|SS|aw|ss)\s*(\d{2})?")
SEASON_CODE = {"秋冬": "AW", "春夏": "SS", "春季": "SS", "夏季": "SS",
               "秋季": "AW", "冬季": "AW", "AW": "AW", "SS": "SS",
               "aw": "AW", "ss": "SS"}

# 切词：连续的中文算一段，英文/数字算一段
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-]*|[一-鿿]+")
# 停用词：切出来但不该当检索词的
STOP = {"的", "和", "或", "带", "有", "一处", "左右", "以内", "不要", "不接受",
        "岁", "款", "个", "方向", "元", "系列", "感", "风", "面料", "颜色"}


def season_code(text):
    """「2026 秋冬」→「AW26」。认不出来就原样返回。"""
    m = SEASON.search(str(text or ""))
    if not m:
        return str(text or "").strip()
    year, word, short = m.groups()
    code = SEASON_CODE.get(word, "")
    if not code:
        return str(text).strip()
    if year:
        return f"{code}{year[2:]}"
    if short:
        return f"{code}{short}"
    return code


# 「不设限」是一个决定，不是一个值 —— 陈主管明确放开的字段，这里不能当
# 检索词切碎，否则「由佘吉在趋势方向里提」会整句进关键词矩阵。
NO_CONSTRAINT = re.compile(r"^\s*[（(]?(不设限|不限|无限制|不指定|开放|由佘吉|待提案)")


def split_terms(value):
    """一个字段值切成词。「Oversized 落肩 / 收腰」→ [Oversized, 落肩, 收腰]"""
    if NO_CONSTRAINT.match(str(value or "")):
        return []
    out = []
    for piece in re.split(r"[、,，/｜|+＋]|\s{2,}", str(value or "")):
        for token in TOKEN.findall(piece):
            token = token.strip()
            if not token or token in STOP:
                continue
            if len(token) == 1 and not '\u4e00' <= token <= '\u9fff':
                continue          # 「T恤」切出来的那个孤立 T，不是检索词
            out.append(token)
    return out


def translate(term):
    """返回 (英文, 是否查到)。查不到就把原词返回并标 False。"""
    if re.fullmatch(r"[A-Za-z0-9\-\s]+", term):
        return term.lower(), True          # 本来就是英文
    if term in LEXICON:
        return LEXICON[term], True
    # 复合词逐段拆：「女装毛衣」是 womenswear + sweater，两段都得留。
    # 只取第一个命中的段会把限定词丢掉 —— 剩一个 sweater，检索面大一个数量级。
    parts, i = [], 0
    while i < len(term):
        for size in range(min(4, len(term) - i), 1, -1):
            chunk = term[i:i + size]
            if chunk in LEXICON:
                parts.append(LEXICON[chunk])
                i += size
                break
        else:
            i += 1
    if parts:
        deduped = []
        for part in parts:
            if part not in deduped:
                deduped.append(part)
        return " ".join(deduped), True
    return term, False


# --------------------------------------------------------------------------
# 关键词矩阵
# --------------------------------------------------------------------------


def build_keywords(fields):
    """按角色分三层：主词（品类）、修饰词（廓形/色彩/面料）、限定词（季节/客群）。

    分层是为了组词的时候控制条数 —— 全排列会炸，主词 × 单个修饰词才是
    检索站上真正能出结果的粒度。
    """
    core, modifiers, qualifiers, unmapped = [], [], [], []
    # 中文平台（淘宝、小红书）要用原词搜，翻成英文在那边什么也搜不到。
    # 所以译文和原词两份都留着，出选品清单时按渠道的 lang 取。
    core_cn, modifiers_cn = [], []

    def collect(field_name, bucket, bucket_cn=None):
        for term in split_terms(fields.get(field_name)):
            if bucket_cn is not None and term not in bucket_cn:
                bucket_cn.append(term)
            en, ok = translate(term)
            if not ok:
                unmapped.append((field_name, term))
                continue
            if en not in bucket:
                bucket.append(en)

    collect("品类", core, core_cn)
    for name in ("廓形", "面料"):
        collect(name, modifiers, modifiers_cn)
    collect("色彩倾向", modifiers, modifiers_cn)
    for name in ("目标客群",):
        collect(name, qualifiers)

    season = season_code(fields.get("季节"))
    return {
        "core": core,
        "core_cn": core_cn,
        "modifiers_cn": modifiers_cn,
        "modifiers": modifiers,
        "qualifiers": qualifiers,
        "season": season,
        "unmapped": unmapped,
    }


def build_directions(kw, directions, target):
    """趋势单按方向拆。

    陈主管把廓形色彩都放开时，修饰词是空的，组出来只有一条 `sweater AW27`
    ——等于没搜。这种单的结构是「N 个趋势方向」，每个方向自带一组词，
    配额也按方向平摊，这样节点 02 采回来的图天然是分好组的。

    方向由人提（陈主管或佘吉），不是脚本编的；脚本只负责组词和分配额。
    """
    if not directions:
        return []
    share = target // len(directions)
    extra = target - share * len(directions)
    out = []
    for i, direction in enumerate(directions):
        terms = direction.get("terms", [])
        queries = []
        for core in kw["core"] or ["knitwear"]:
            for term in terms:
                q = f"{term} {core} {kw['season']}".strip()
                if q not in queries:
                    queries.append(q)
        out.append({
            "name": direction.get("name", f"Direction {i + 1}"),
            "terms": terms,
            "queries": queries,
            "directions": directions,
            "quota": share + (extra if i == 0 else 0),
        })
    return out


def build_queries(kw, brands, limit=24):
    """主词 × 修饰词 + 季节。品牌单独一组。"""
    queries = []
    season = kw["season"]
    for c in kw["core"] or ["fashion"]:
        base = f"{c} {season}".strip()
        if base not in queries:
            queries.append(base)
        for m in kw["modifiers"]:
            q = f"{m} {c} {season}".strip()
            if q not in queries:
                queries.append(q)
    for q in kw["qualifiers"]:
        for c in kw["core"][:1]:
            combo = f"{q} {c}".strip()
            if combo not in queries:
                queries.append(combo)
    brand_queries = [f"{b} {c}".strip() for b in brands for c in (kw["core"] or [""])]
    return queries[:limit], brand_queries


# --------------------------------------------------------------------------
# 配额
# --------------------------------------------------------------------------


# 英文 → 中文反查。一个英文词可能有多个中文说法（cable knit ↔ 绞花/麻花），
# 都留着 —— 中文电商搜索对同义词很敏感，搜「绞花」和搜「麻花」出来的货不一样。
REVERSE = {}
for _cn, _en in LEXICON.items():
    REVERSE.setdefault(_en, []).append(_cn)


def queries_cn(kw, directions):
    """中文平台的检索词。

    中文电商搜索吃的是短词组，「AW27」这种季节代码在淘宝上搜不出东西，
    换成「2027秋冬」也不如直接搜品类 + 特征 —— 季节靠上架时间筛，不靠词。
    """
    head = (kw["core_cn"] or ["毛衣"])[0]
    out, no_chinese = [], []

    for core in kw["core_cn"] or ["毛衣"]:
        if core not in out:
            out.append(core)
        for term in kw["modifiers_cn"]:
            combo = f"{term}{core}"
            if combo not in out:
                out.append(combo)

    # 方向的检索词是英文（给 Pinterest / WGSN 用的），这里必须反查回中文。
    # 查不到的**不放英文进去** —— 在淘宝搜 "pointelle" 什么也搜不出来，
    # 放进清单只会让人白试一轮。列到末尾请人自己判断。
    for direction in directions:
        for term in direction.get("terms", []):
            words = REVERSE.get(term)
            if not words:
                if term not in no_chinese:
                    no_chinese.append(term)
                continue
            for word in words:
                combo = f"{word}{head}"
                if combo not in out:
                    out.append(combo)
    return out, no_chinese


CHANNEL_HINTS = {
    "taobao": [
        "按「销量」排序看什么好卖，按「新品」排序看什么刚上",
        "价格带筛到需求单给的区间，超出的不看 —— 不同价位的款型逻辑不一样",
        "记下：商品标题、价格、月销、店铺名、商品链接",
        "商品图版权属卖家，只作内部市场参考，不进任何交付物",
    ],
    "xiaohongshu": [
        "看笔记的互动量（赞藏评），高互动的说明需求端认这个点",
        "同时记下评论区在问什么 —— 「显胖吗」「起球吗」这类是真实痛点",
        "记下：笔记标题、作者、发布时间、互动量、笔记链接",
        "笔记图是个人创作者的作品，**绝不能进任何交付物**，搬运和洗稿的法律风险是实的",
    ],
    "wgsn": [
        "筛 Southern Hemisphere / Australia —— 站内默认北半球口径",
        "记下：报告名 + 发布日期、栏目路径、图片 ID 或页面 URL",
        "只作方向和克重的校准，不当素材用",
    ],
}


def write_picklists(folder, meta, kw, channels, directions, target):
    """给每个 manual 渠道出一份选品清单。

    手动渠道的配额是**排给人的任务**，不是佘吉的任务。清单要具体到他打开
    浏览器就能照着做，不用回来问。
    """
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    written = []

    for channel in channels:
        if channel["mode"] != "manual":
            continue
        zh = channel.get("lang") == "zh"
        terms, no_chinese = queries_cn(kw, directions) if zh else (None, [])

        lines = [f"# {channel['name']} 选品清单",
                 f"**{meta.get('req_id', '')} · {meta.get('project', '')}**", "",
                 f"给做这件事的同事。佘吉不登录、不抓取——目标 **{channel['quota']} 张**，",
                 f"收完放进 `02-assets/` 对应方向的目录，再跑 collect.py 收进台账。", "",
                 f"> **{channel['license']}**", "",
                 f"{channel['note']}", "", "## 搜什么", ""]

        if zh:
            lines.append("中文平台用中文搜，英文检索式在这边搜不出东西。")
            lines.append("")
            for term in terms[:24]:
                lines.append(f"- `{term}`")
            if no_chinese:
                lines += ["", "**这几个方向词没有对应中文，词表里查不到 —— 我不编：**", ""]
                for term in no_chinese:
                    lines.append(f"- `{term}`　你按理解换个说法搜，"
                                 f"确认后加进 search_plan.py 的 LEXICON，下次自动有")
        else:
            lines.append("按方向分，每个方向的额度均摊：")
            lines.append("")
            for direction in directions:
                share = max(1, channel["quota"] // max(1, len(directions)))
                lines.append(f"### {direction['name']}　约 {share} 张")
                for query in direction["queries"][:4]:
                    lines.append(f"- `{query}`")
                lines.append("")

        lines += ["", "## 怎么挑", ""]
        for hint in CHANNEL_HINTS.get(channel["key"], ["记下每张图的来源链接和作者"]):
            lines.append(f"- {hint}")
        lines += ["", "## 出处怎么记", "",
                  "在导出目录放一份 `sources.csv`，**逐张**登记：", "",
                  "```csv", "filename,source_url,author,note",
                  "001.jpg,<链接>,<作者或店铺>,<标题>", "```", "",
                  "没有它，出处只记到批次级别，Sources 页写不出单张来源。"]

        path = root / f"picklist-{channel['key']}.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(path)
    return written


def allocate(target, keys=None):
    """按权重把目标图量分到各渠道，余数补给权重最大的那个。"""
    active = [c for c in CHANNELS if c["mode"] != "off"]
    if keys:
        active = [c for c in active if c["key"] in keys]
    total_weight = sum(c["weight"] for c in active) or 1.0

    plan = []
    assigned = 0
    for c in active:
        n = int(target * c["weight"] / total_weight)
        assigned += n
        plan.append(dict(c, quota=n))
    if plan and assigned < target:
        biggest = max(plan, key=lambda c: c["weight"])
        biggest["quota"] += target - assigned
    return plan


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------


def render(meta, fields, kw, queries, brand_queries, channels, target, directions=()):
    out = []
    head = " · ".join(str(meta[k]) for k in ("req_id", "client", "project") if meta.get(k))
    out.append("=" * 62)
    out.append("检索策略　节点 01")
    out.append(head)
    out.append("=" * 62)

    out.append(f"\n【目标图量】{target} 张（陈主管设定）")

    out.append("\n【关键词矩阵】")
    out.append(f"  季节　　{kw['season'] or '—'}")
    out.append(f"  主词　　{'、'.join(kw['core']) or '—'}")
    out.append(f"  修饰词　{'、'.join(kw['modifiers']) or '—'}")
    if kw["qualifiers"]:
        out.append(f"  限定词　{'、'.join(kw['qualifiers'])}")

    if directions:
        out.append(f"\n【趋势方向 {len(directions)} 个】← 方向本身也要陈主管点头")
        for i, d in enumerate(directions, 1):
            out.append(f"  {i}. {d['name']}　配额 {d['quota']} 张")
            for q in d["queries"]:
                out.append(f"       {q}")

    out.append(f"\n【通用检索式 {len(queries)} 条】")
    for i, q in enumerate(queries, 1):
        out.append(f"  {i:>2}. {q}")
    if brand_queries:
        out.append("\n【品牌官网】")
        for q in brand_queries:
            out.append(f"      {q}")

    out.append("\n【渠道分配】")
    for c in channels:
        out.append(f"  {c['name']}　{c['quota']} 张　[{c['mode']}]　{c['license']}")
        out.append(f"      {c['note']}")
    blocked = [c for c in CHANNELS if c["mode"] == "off"]
    for c in blocked:
        out.append(f"  {c['name']}　不采集　{c['license']}")
        out.append(f"      {c['note']}")

    if kw["unmapped"]:
        out.append(f"\n【需补英文 {len(kw['unmapped'])} 项】← 词表里没有，我没有猜")
        for field_name, term in kw["unmapped"]:
            out.append(f"  {field_name}：{term}")
        out.append("  补法：确认英文后加进 search_plan.py 的 LEXICON，下次自动生效")

    out.append("\n" + "-" * 62)
    if kw["unmapped"]:
        out.append(f"状态：有 {len(kw['unmapped'])} 个词没翻译，补齐后再进节点 02 采集")
    elif not kw["core"]:
        out.append("状态：没有主词 —— 需求单的「品类」是空的，先回节点 00")
    else:
        out.append("状态：待陈主管确认检索式与配额，确认后进节点 02 采集")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="节点 01 · 检索策略")
    parser.add_argument("intake", nargs="?", help="节点 00 的 intake.json")
    parser.add_argument("--target", type=int, default=40, help="目标图量，默认 40")
    parser.add_argument("--channels", help="只用这些渠道，逗号分隔（brand,pinterest,wgsn）")
    parser.add_argument("--limit", type=int, default=24, help="检索式条数上限")
    parser.add_argument("-o", "--output", help="写出 plan.json，供节点 02 读取")
    parser.add_argument("--queries", action="store_true", help="只打检索式，一行一条")
    parser.add_argument("--picklists", metavar="DIR", help="给每个人工渠道出一份选品清单")
    parser.add_argument("--lexicon", action="store_true", help="打印词表后退出")
    args = parser.parse_args()

    print(f"search_plan 版本 {VERSION}", file=sys.stderr)

    if args.lexicon:
        for cn, en in LEXICON.items():
            print(f"{cn:<8}{en}")
        return

    if not args.intake:
        parser.error("需要节点 00 的 intake.json")

    data = json.loads(Path(args.intake).read_text(encoding="utf-8-sig"))
    # intake.json 用 fields，brief.json 用 brief，两种都收
    fields = data.get("fields") or data.get("brief") or {}
    meta = data.get("meta", {})

    kw = build_keywords(fields)
    brands = split_terms(fields.get("目标品牌"))
    queries, brand_queries = build_queries(kw, brands, args.limit)
    keys = [k.strip() for k in args.channels.split(",")] if args.channels else None
    channels = allocate(args.target, keys)

    if args.queries:
        for q in queries + brand_queries:
            print(q)
        return

    directions = build_directions(kw, data.get("directions", []), args.target)
    print(render(meta, fields, kw, queries, brand_queries, channels, args.target, directions))

    if args.output:
        plan = {
            "meta": dict(meta, node="01-search-plan", version=VERSION),
            "target": args.target,
            "keywords": {k: kw[k] for k in ("core", "modifiers", "qualifiers", "season")},
            "queries": queries,
            "directions": directions,
            "brand_queries": brand_queries,
            "channels": [
                {k: c[k] for k in ("key", "name", "mode", "quota", "license", "note")}
                for c in channels
            ],
            "unmapped": [{"字段": f, "词": t} for f, t in kw["unmapped"]],
            "approved": False,
        }
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写出 {args.output}　（approved=false，陈主管确认后手改成 true）")

    if args.picklists:
        written = write_picklists(args.picklists, meta, kw, channels, directions, args.target)
        for path in written:
            print(f"选品清单：{path}")
        if not written:
            print("没有人工渠道，不用出选品清单")


if __name__ == "__main__":
    main()
