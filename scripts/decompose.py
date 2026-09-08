#!/usr/bin/env python3
"""
节点 03 · 风格解构。

把节点 02 收回来的图，变成每个方向的风格要素表——给节点 04 写出图指令用。

**这个节点只做算得出来的部分。**

  算得出来：配色（从图里提）、词频（从商品标题里统计）、渠道构成、图量
  算不出来：廓形、领型、克重、工艺——这些要看图，脚本看不了

算不出来的留空，标「待填」，由人或佘吉看图补。脚本编一个「Oversized」出来，
后面整条链都会跟着它跑偏，而且没人会发现那是编的。

词频是个交叉检查：方向叫「Textural Craft」，收回来的图标题里却一次都没出现
cable / mohair，说明采集跑偏了，该回节点 02 而不是往下走。

用法：
    python -X utf8 decompose.py --plan plan.json --assets 02-assets/ -o 03-style/
    python -X utf8 decompose.py --plan plan.json --assets 02-assets/ --colors-only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

VERSION = "2026-09-08c"

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

# 人来填的那几项。留空不是偷懒，是这几项脚本判断不了。
JUDGEMENT = [
    ("廓形", "Oversized 落肩 / 修身 / 茧型…这一堆图的共性是什么"),
    ("领型", "圆领 / V 领 / 高领 / 立领"),
    ("克重", "细针 12gg / 中针 7gg / 粗针 3gg，或者 gsm"),
    ("工艺", "绞花 / 抽针 / 提花 / 嵌花 / 起绒"),
    ("袖型", "落肩 / 插肩 / 装袖 / 灯笼"),
    ("长度", "短款 / 常规 / 长款，或者具体厘米"),
]

TOKEN = re.compile(r"[A-Za-z][A-Za-z\-]{2,}|[一-鿿]{2,4}")
STOP = {"the", "and", "with", "for", "new", "women", "womens", "sweater", "knit",
        "knitwear", "jumper", "top", "size", "colour", "color", "季", "新款",
        "女装", "毛衣", "针织", "包邮", "现货", "秋冬", "春夏"}


# --------------------------------------------------------------------------
# 配色
# --------------------------------------------------------------------------


def dominant_colours(path, buckets=8):
    """一张图的主色。

    产品图基本是白底棚拍，直接量化的话白色会吃掉一大半。所以只取中央那块
    （模特和衣服在的地方），再把接近纯白的桶丢掉。

    这不完美：白色的毛衣会被一起丢掉。所以下面统计时会记下丢了多少，
    丢得太多会提示——**宁可提示不准，也不要悄悄给出一份错的配色**。
    """
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((240, 240))
        width, height = image.size
        image = image.crop((int(width * .2), int(height * .15),
                            int(width * .8), int(height * .9)))
        quantized = image.quantize(colors=buckets, method=Image.Quantize.MEDIANCUT)
        palette = quantized.getpalette()
        counts = sorted(quantized.getcolors(), reverse=True)

    total = sum(count for count, _ in counts) or 1
    kept, dropped = [], 0
    for count, index in counts:
        r, g, b = palette[index * 3: index * 3 + 3]
        if min(r, g, b) > 236:                 # 棚拍白底
            dropped += count
            continue
        kept.append(((r, g, b), count))
    return kept, dropped / total


def merge_colours(entries, tolerance=42):
    """把相近的颜色并到一起，不然出来六个深浅差不多的灰。"""
    merged = []
    for (colour, weight) in sorted(entries, key=lambda item: -item[1]):
        for existing in merged:
            near = sum((a - b) ** 2 for a, b in zip(colour, existing["rgb"])) ** 0.5
            if near < tolerance:
                total = existing["weight"] + weight
                existing["rgb"] = tuple(
                    round((a * existing["weight"] + b * weight) / total)
                    for a, b in zip(existing["rgb"], colour))
                existing["weight"] = total
                break
        else:
            merged.append({"rgb": tuple(colour), "weight": weight})
    return merged


def hexed(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def palette_of(paths, top=6):
    if not HAVE_PIL:
        return [], 0.0, "没装 Pillow，提不了配色：pip install Pillow"
    entries, white_share, failed = [], [], 0
    for path in paths:
        try:
            kept, white = dominant_colours(path)
        except (OSError, ValueError):
            failed += 1
            continue
        entries.extend(kept)
        white_share.append(white)
    if not entries:
        return [], 0.0, f"{failed} 张图读不出来" if failed else "没有图"

    merged = merge_colours(entries)
    total = sum(item["weight"] for item in merged) or 1
    palette = [{"hex": hexed(item["rgb"]), "rgb": list(item["rgb"]),
                "share": round(item["weight"] / total, 3)}
               for item in merged[:top]]
    average_white = sum(white_share) / len(white_share)
    warning = ""
    if average_white > 0.55:
        warning = (f"平均 {average_white:.0%} 的像素是白底被丢掉的。"
                   "如果这批本来就是白色/米色的款，这份配色会偏——人工核一眼")
    return palette, average_white, warning


# --------------------------------------------------------------------------
# 词频
# --------------------------------------------------------------------------


def terms_of(items, top=12):
    """商品标题里反复出现的词。方向对不对，看这个最快。"""
    counter = Counter()
    for item in items:
        # 去掉扩展名，不然 jpg / png 会稳居高频词榜首，把真正的信号挤下去
        name = re.sub(r"\.(jpe?g|png|webp|tiff?|bmp)$", "", item.get("original_name", ""), flags=re.I)
        text = f"{item.get('note', '')} {name}".lower()
        for token in TOKEN.findall(text):
            if token not in STOP and not token.isdigit():
                counter[token] += 1
    return [{"term": term, "count": count} for term, count in counter.most_common(top)]


def coverage(direction, terms):
    """方向自己的检索词，在收回来的图里出现了几个。

    命中太少 = 采集跑偏了，该回节点 02，不是硬着头皮往下走。
    """
    seen = {entry["term"] for entry in terms}
    wanted = [t.lower() for t in direction.get("terms", [])]
    hit = [t for t in wanted if any(word in seen for word in t.split())]
    return hit, [t for t in wanted if t not in hit]


# --------------------------------------------------------------------------


def build(plan, ledger, assets):
    directions = plan.get("directions", [])
    items = ledger.get("items", [])
    out = []

    for direction in directions:
        mine = [i for i in items if i.get("direction") == direction["name"]]
        paths = [Path(assets) / i["file"] for i in mine]
        paths = [p for p in paths if p.exists()]
        palette, white, warning = palette_of(paths)
        terms = terms_of(mine)
        hit, miss = coverage(direction, terms)

        out.append({
            "name": direction["name"],
            "quota": direction.get("quota", 0),
            "collected": len(mine),
            "channels": dict(Counter(i.get("channel", "?") for i in mine)),
            "palette": palette,
            "palette_warning": warning,
            "terms": terms,
            "terms_hit": hit,
            "terms_missing": miss,
            # 这几项脚本判断不了，留给人
            "judgement": {name: "" for name, _ in JUDGEMENT},
        })
    return out


def render(meta, blocks):
    lines = ["=" * 62, "风格解构　节点 03",
             " · ".join(str(meta[k]) for k in ("req_id", "project") if meta.get(k)),
             "=" * 62]

    for block in blocks:
        lines.append(f"\n【{block['name']}】　{block['collected']}/{block['quota']} 张　"
                     + "　".join(f"{k} {v}" for k, v in block["channels"].items()))
        if not block["collected"]:
            lines.append("  还没收到图，跳过")
            continue

        if block["palette"]:
            lines.append("  配色　" + "　".join(
                f"{c['hex']} {c['share']:.0%}" for c in block["palette"]))
        if block["palette_warning"]:
            lines.append(f"  ⚠ {block['palette_warning']}")

        if block["terms"]:
            lines.append("  高频词　" + "、".join(
                f"{t['term']}×{t['count']}" for t in block["terms"][:8]))
        if block["terms_missing"]:
            lines.append(f"  ⚠ 方向词一次没出现：{'、'.join(block['terms_missing'])}"
                         "　—— 采集可能跑偏了，回节点 02 看看")

        blank = [name for name, value in block["judgement"].items() if not value]
        if blank:
            lines.append(f"  待填 {len(blank)} 项：{'、'.join(blank)}"
                         "　—— 这几项要看图，脚本判断不了")

    lines += ["\n" + "-" * 62]
    unfilled = sum(1 for b in blocks for v in b["judgement"].values() if not v)
    empty = [b["name"] for b in blocks if not b["collected"]]
    if empty:
        lines.append(f"状态：{len(empty)} 个方向还没图，先回节点 02 收齐")
    elif unfilled:
        lines.append(f"状态：算得出来的都算完了，还有 {unfilled} 项要看图填，"
                     "填完再进节点 04 出图指令")
    else:
        lines.append("状态：风格要素表齐了，可以进节点 04")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="节点 03 · 风格解构")
    parser.add_argument("--plan", required=True, help="节点 01 的 plan.json")
    parser.add_argument("--assets", required=True, help="节点 02 的 02-assets 目录")
    parser.add_argument("-o", "--output", help="写出 style.json 的目录")
    parser.add_argument("--colors-only", action="store_true", help="只打配色")
    args = parser.parse_args()

    print(f"decompose 版本 {VERSION}", file=sys.stderr)
    if not HAVE_PIL:
        print("没装 Pillow，配色提不了：pip install Pillow", file=sys.stderr)

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8-sig"))
    ledger_path = Path(args.assets) / "ledger.json"
    if not ledger_path.exists():
        sys.exit(f"找不到 {ledger_path} —— 节点 02 还没收过图")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8-sig"))

    blocks = build(plan, ledger, args.assets)

    # 已填的判断项要先并回来，再渲染。
    # 原来只在写文件那一支合并，不给 -o 时就照着空表报"还有 30 项要填" ——
    # 文件里明明填好了，状态却说没填，这种不一致比少个功能更糟。
    existing = Path(args.output or Path(args.assets).parent / "03-style") / "style.json"
    if existing.exists():
        old_values = {b["name"]: b.get("judgement", {})
                      for b in json.loads(existing.read_text(encoding="utf-8-sig")).get("directions", [])}
        for block in blocks:
            for key, value in old_values.get(block["name"], {}).items():
                if value and key in block["judgement"]:
                    block["judgement"][key] = value

    if args.colors_only:
        for block in blocks:
            print(f"{block['name']}")
            for colour in block["palette"]:
                print(f"  {colour['hex']}  {colour['share']:.0%}")
        return

    meta = plan.get("meta", {})
    print(render(meta, blocks))

    if args.output:
        folder = Path(args.output)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "style.json"
        path.write_text(json.dumps(
            {"meta": dict(meta, node="03-style", version=VERSION),
             "judgement_fields": [{"name": n, "hint": h} for n, h in JUDGEMENT],
             "directions": blocks},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写出 {path}　（待填项直接在这个文件里补）")


if __name__ == "__main__":
    main()
