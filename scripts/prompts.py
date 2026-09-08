#!/usr/bin/env python3
"""
节点 04 · 出图指令。

把节点 03 的风格要素表变成各生图工具能直接用的 prompt。

一款出**两条**：正面和背面。交付 PPT 一页放正反两张图，所以 prompt 从这里
就是成对生成的，不是生完正面再想办法补背面。

工具表在 TOOLS 里：
  midjourney  没有官方 API。第三方中转和 Discord self-bot 都违反 ToS，
              封号是真的 —— 佘吉只出 prompt，人粘贴进 Discord。
  ideogram    有官方 API，走 IDEOGRAM_API_KEY 环境变量。
              **key 不进仓库、不进聊天**，只在环境变量里。

用法：
    python -X utf8 prompts.py --style style.json --brief brief.json -o 04-prompts/
    python -X utf8 prompts.py --style style.json --brief brief.json --tool ideogram
"""
from __future__ import annotations

import argparse
import colorsys
import json
import re
import sys
from pathlib import Path

VERSION = "2026-09-08b"

# 工具表。mode 决定佘吉能不能自己跑；license 是能不能进客户交付页。
# 条款会变 —— 这里的说明是 2026-09 查的，签约或大批量跑之前自己再核一遍。
# 加工具就往这里加一条，**加之前先确认商用授权**。
TOOLS = {
    "ideogram": {
        "name": "Ideogram",
        "mode": "auto-api",
        "env": "IDEOGRAM_API_KEY",
        "role": "主力出款图",
        "license": "付费档含商用授权，生成物归你；免费档产出公开且商用受限",
        "note": "官方 API，按张计费（V4 约 $0.03 Turbo / $0.06 默认 / $0.10 Quality）。"
                "Character Reference 可以拉正反一致性。公司已有专业版月付。",
        "file": "ideogram.txt",
    },
    "firefly": {
        "name": "Adobe Firefly",
        "mode": "auto-api",
        "env": "FIREFLY_CLIENT_ID / FIREFLY_CLIENT_SECRET",
        "role": "交付兜底",
        "license": "训练数据是 Adobe Stock + 授权/公有领域内容；合格档位提供 IP 赔偿",
        "note": "唯一给 IP 赔偿的一家。真正要进客户交付页的图用它最稳 —— "
                "赔偿条款按档位和合同走，签之前让法务看一眼。",
        "file": "firefly.txt",
    },
    "patterned": {
        "name": "PatternedAI",
        "mode": "manual",
        "role": "面料花型",
        "license": "生成的花型 royalty-free，可无限商用",
        "note": "垂直做无缝循环，能把面料照片转成可循环花型，还能矢量化成 SVG "
                "给圆网/丝网印。这一块没有替代品。API 官网早期写 coming soon，"
                "当前状态未确认，先按人工用。",
        "file": "patterned.txt",
    },
    "midjourney": {
        "name": "Midjourney",
        "mode": "manual",
        "role": "概念探索",
        "license": "付费订阅含商用",
        "note": "没有官方 API。第三方中转、Discord self-bot 都违反 ToS，别碰。"
                "佘吉出 prompt，人粘贴进 Discord。审美上限高，适合前期发散。",
        "file": "midjourney.txt",
    },
    "jimeng": {
        "name": "即梦 AI",
        "mode": "manual",
        "role": "中文提示词",
        "license": "平台自带授权，可批量下载 PDF 授权书",
        "note": "设计师直接用中文描述，省一道翻译。授权书能下载这点对交付很实用。"
                "API 走火山方舟，接入成本另算，先按人工用。",
        "file": "jimeng.txt",
    },
}

# 明确不进工具表的，理由记在这里，免得下次又有人提
NOT_USED = {
    "leonardo-free": "Leonardo 免费档：平台保留对生成图的使用、复制、修改、分发权利，"
                     "不能用于客户交付。要用就上付费档。",
    "sd-self-host": "Stable Diffusion 自建：唯一能完全离线、数据不出公司的选项，"
                    "但要 GPU 和运维，且各版本权重的商用条款不一样"
                    "（SDXL 和 SD3 不同），上之前逐个核。",
}

# 色相分段。边界是约定俗成的那套，不是我编的，但也不是标准——
# 有异议直接改这张表。
HUES = [
    (15, "red"), (40, "orange"), (65, "yellow"), (150, "green"),
    (195, "teal"), (255, "blue"), (285, "violet"), (330, "magenta"), (361, "red"),
]


def colour_words(hex_value):
    """#BFB7AA → "soft light warm-beige"。

    prompt 里放 #BFB7AA 这种十六进制，生图模型基本不认；给它形容词才有用。
    这里的映射是死的规则，不是猜——同一个 hex 每次出来都一样。
    """
    value = hex_value.lstrip("#")
    r, g, b = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    hue *= 360

    if saturation < 0.08:
        base = "grey" if 0.2 < lightness < 0.8 else ("near-black" if lightness <= 0.2 else "off-white")
    else:
        base = next(name for edge, name in HUES if hue < edge)
        if base in ("orange", "yellow") and saturation < 0.35:
            base = "beige" if lightness > 0.55 else "taupe"

    tone = ("deep" if lightness < 0.28 else "dark" if lightness < 0.45
            else "mid" if lightness < 0.62 else "light" if lightness < 0.82 else "pale")
    intensity = ("muted" if saturation < 0.25 else "soft" if saturation < 0.5
                 else "rich" if saturation < 0.75 else "vivid")
    return f"{intensity} {tone} {base}"


def palette_words(palette, top=3):
    """去重——#BFB7AA 和 #D7D5CB 都落在"muted light beige"上，
    重复写进 prompt 只是噪声。"""
    out = []
    for colour in palette:
        words = colour_words(colour["hex"])
        if words not in out:
            out.append(words)
        if len(out) >= top:
            break
    return out


# --------------------------------------------------------------------------
# prompt 组装
# --------------------------------------------------------------------------

# 各家都吃这一套：主体 → 服装细节 → 面料 → 配色 → 视角 → 拍摄条件。
# 顺序有讲究：靠前的词权重高，所以款式特征放前面，棚拍条件放最后。
BASE_VIEW = {
    "front": "front view, facing camera, arms relaxed at sides",
    "back": "back view, facing away from camera, same garment and colourway",
}
SHOT = ("full-body fashion lookbook photograph, single female model, "
        "plain seamless white studio background, soft even studio lighting, "
        "sharp focus on garment texture, no props")


def one_prompt(brief, direction, judgement, palette, view, index, terms=()):
    """一条 prompt。空的判断项直接跳过——不给模型编一个。

    同一个方向的几款要**互不相同**，否则生出来是四张重复图。区分的两个轴
    都来自已有数据，不是我编的：
      - 主色轮换：第 N 款用调色板里第 N 个颜色打头
      - 特征轮换：第 N 款突出方向检索词里的第 N 个（cable knit / pointelle…）
    想要比这更大的差异，那是设计决定，去 style.json 里分成不同方向或加要素。
    """
    bits = []
    category = brief.get("品类", "knitwear")
    bits.append(f"{judgement.get('廓形') or ''} {category}".strip())

    if terms:
        bits.append(terms[(index - 1) % len(terms)])

    for key in ("领型", "袖型", "长度", "工艺", "克重"):
        if judgement.get(key):
            bits.append(judgement[key])

    words = palette_words(palette)
    if words:
        # 主色轮换：每款换一个打头色，其余按原顺序跟在后面
        lead = (index - 1) % len(words)
        ordered = words[lead:] + words[:lead]
        bits.append("colourway: " + ", ".join(ordered))

    bits.append(BASE_VIEW[view])
    bits.append(SHOT)

    # 去重：轮换的特征词可能和已填的要素撞上（crew neck 既是方向词又是领型），
    # 重复写两遍在 prompt 里是加权，会把这一项放大到不该有的份量
    seen, out = set(), []
    for part in bits:
        part = (part or "").strip()
        key = part.lower()
        if part and key not in seen:
            seen.add(key)
            out.append(part)
    return ", ".join(out)


def midjourney_line(prompt, ratio="3:4", version="", stylize=250, extra=""):
    """MJ 的参数拼在 prompt 尾巴上。

    --style raw 是关键：不加的话 MJ 会往"好看"的方向美化，出来的是海报不是
    可开发的款。--no text 挡掉它老爱加的字和 logo。
    """
    tail = [f"--ar {ratio}", "--style raw", f"--s {stylize}",
            "--no text, watermark, logo, collage, multiple views"]
    if version:
        tail.append(f"--v {version}")
    if extra:
        tail.append(extra)
    return f"/imagine prompt: {prompt} {' '.join(tail)}"


def build(style, brief, tools, per_direction, ratio, mj_version, plan=None):
    # 方向的检索词在 plan.json 里，style.json 不带；用来做款与款之间的区分轴
    terms_by_name = {d["name"]: d.get("terms", [])
                     for d in (plan or {}).get("directions", [])}
    blocks = []
    for direction in style.get("directions", []):
        judgement = {k: v for k, v in direction.get("judgement", {}).items() if v}
        missing = [k for k, v in direction.get("judgement", {}).items() if not v]
        terms = terms_by_name.get(direction["name"], [])
        styles = []
        for index in range(1, per_direction + 1):
            entry = {"style_no": f"{index:03d}",
                     "feature": terms[(index - 1) % len(terms)] if terms else "",
                     "views": {}}
            for view in ("front", "back"):
                text = one_prompt(brief, direction, judgement, direction.get("palette", []),
                                  view, index, terms)
                entry["views"][view] = {
                    "prompt": text,
                    "midjourney": midjourney_line(text, ratio, mj_version),
                }
            styles.append(entry)
        blocks.append({
            "direction": direction["name"],
            "missing_judgement": missing,
            "palette": [c["hex"] for c in direction.get("palette", [])[:3]],
            "palette_words": palette_words(direction.get("palette", [])),
            "styles": styles,
        })
    return blocks


def render(blocks, tools, per_direction):
    lines = ["=" * 62, "出图指令　节点 04", "=" * 62,
             f"\n{len(blocks)} 个方向 × {per_direction} 款 × 正反 2 张 = "
             f"{len(blocks) * per_direction * 2} 条 prompt"]

    lines.append("\n【工具】")
    for key in tools:
        tool = TOOLS[key]
        lines.append(f"  {tool['name']:<12}[{tool['mode']}]")
        lines.append(f"      {tool['note']}")

    blocked = [b for b in blocks if b["missing_judgement"]]
    for block in blocks:
        lines.append(f"\n【{block['direction']}】")
        if block["palette_words"]:
            lines.append(f"  配色词　{'; '.join(block['palette_words'])}"
                         f"　（{'、'.join(block['palette'])}）")
        if block["missing_judgement"]:
            lines.append(f"  ⚠ 风格要素还差 {len(block['missing_judgement'])} 项没填："
                         f"{'、'.join(block['missing_judgement'])}")
            lines.append("     这些 prompt 缺了这几项，生出来的款不受控 —— 回节点 03 补")
        features = [s["feature"] for s in block["styles"] if s["feature"]]
        if features:
            lines.append(f"  四款分别突出　{'、'.join(features)}")
        elif len(block["styles"]) > 1:
            lines.append("  ⚠ 没给 --plan，几款之间只有配色轮换的差别 —— "
                         "加上 --plan plan.json 才能按方向特征区分")
        sample = block["styles"][0]["views"]["front"]["prompt"]
        lines.append(f"  样例　{sample[:110]}…")

    lines.append("\n" + "-" * 62)
    if blocked:
        lines.append(f"状态：{len(blocked)} 个方向的风格要素没填全，"
                     "补完节点 03 再批量生成，否则白跑一轮")
    else:
        lines.append("状态：prompt 齐了，等主管过一遍再进节点 05 批量生成")
    return "\n".join(lines)


def write_files(folder, blocks, tools, meta):
    """每个工具一个文件，按 TOOLS 表驱动。

    以前这里把 midjourney 和 ideogram 写死了，加了新工具却不出文件——
    表里有、产出没有，是最容易被忽略的那种不一致。
    """
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)

    (root / "prompts.json").write_text(json.dumps(
        {"meta": dict(meta, node="04-prompts", version=VERSION),
         "tools": {k: TOOLS[k] for k in tools},
         "not_used": NOT_USED,
         "directions": blocks}, ensure_ascii=False, indent=2), encoding="utf-8")

    written = []
    for key in tools:
        tool = TOOLS[key]
        head = [f"{tool['name']} —— {tool['role']}", tool["note"], ""]
        if tool.get("env"):
            head.insert(2, f"key 放环境变量 {tool['env']}，不进文件、不进仓库。")

        lines = list(head)
        for block in blocks:
            lines.append(f"### {block['direction']}")
            for style in block["styles"]:
                for view, data in style["views"].items():
                    lines.append(f"# {style['style_no']} {view}")
                    # MJ 要带参数行，其余工具吃纯 prompt
                    lines.append(data["midjourney"] if key == "midjourney" else data["prompt"])
            lines.append("")
        (root / tool["file"]).write_text("\n".join(lines), encoding="utf-8")
        written.append(tool["file"])
    return root, written


def main():
    parser = argparse.ArgumentParser(description="节点 04 · 出图指令")
    parser.add_argument("--style", required=True, help="节点 03 的 style.json")
    parser.add_argument("--brief", help="brief.json，取品类等字段")
    parser.add_argument("--plan", help="节点 01 的 plan.json，取方向检索词做款间区分")
    parser.add_argument("--tool", action="append", choices=sorted(TOOLS),
                        help="只出这个工具的，可重复。默认全出")
    parser.add_argument("--per-direction", type=int, default=4, help="每个方向几款")
    parser.add_argument("--ratio", default="3:4", help="画幅，默认 3:4")
    parser.add_argument("--mj-version", default="",
                        help="MJ 版本号，不填就用账号默认（别替账号猜版本）")
    parser.add_argument("-o", "--output", help="写出目录")
    args = parser.parse_args()

    print(f"prompts 版本 {VERSION}", file=sys.stderr)

    style = json.loads(Path(args.style).read_text(encoding="utf-8-sig"))
    brief = {}
    if args.brief and Path(args.brief).exists():
        brief = json.loads(Path(args.brief).read_text(encoding="utf-8-sig")).get("brief", {})

    plan = None
    if args.plan and Path(args.plan).exists():
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8-sig"))

    tools = args.tool or sorted(TOOLS)
    blocks = build(style, brief, tools, args.per_direction, args.ratio,
                   args.mj_version, plan)
    print(render(blocks, tools, args.per_direction))

    if args.output:
        root, written = write_files(args.output, blocks, tools, style.get("meta", {}))
        print(f"\n已写出 {root}/")
        for name in written + ["prompts.json"]:
            print(f"  {name}")

    if any(b["missing_judgement"] for b in blocks):
        sys.exit(1)


if __name__ == "__main__":
    main()
