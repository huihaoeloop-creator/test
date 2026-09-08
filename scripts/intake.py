#!/usr/bin/env python3
"""
节点 00 · 需求接单与结构化。

把陈主管的一句话需求变成一份可签字的《需求确认单》，并直接产出节点 07
（build_deck.py）要的 brief.json。

这一步的价值全在「不猜」：缺的字段要显式列成待确认项交回去问，而不是
自己填一个看起来合理的值。在这里花五分钟消歧，比第 5 节点返工两小时便宜。

用法：
    python -X utf8 intake.py --new -o intake.json          # 生成空白需求单
    python -X utf8 intake.py intake.json                   # 校验并打印确认单
    python -X utf8 intake.py intake.json --history projects/
    python -X utf8 intake.py intake.json --to-brief brief.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

# --------------------------------------------------------------------------
# 字段定义 —— 要加字段直接改这张表，脚本其余部分不用动
# --------------------------------------------------------------------------
# level:
#   required  缺了没法开工，校验直接失败
#   key       缺了能开工但方向会跑偏，自动进待确认项
#   optional  缺了不提
FIELDS = [
    # (字段名, level, 提示/示例)
    ("品类",     "required", "女装夹克 / 针织衫 / 连衣裙"),
    ("季节",     "required", "2026 秋冬 / AW26"),
    ("交付日",   "required", "YYYY-MM-DD"),
    ("廓形",     "key",      "Oversized 落肩 / 修身 / A 摆"),
    ("色彩倾向", "key",      "冷调金属感 + 一处高饱和"),
    ("面料",     "key",      "涤纶塔夫绸 / 80-110 gsm"),
    ("目标客群", "key",      "18-25 岁"),
    ("价格带",   "key",      "¥299-459"),
    ("工艺禁忌", "optional", "不接受烫钻、不接受大面积刺绣"),
    ("数量",     "optional", "3 个方向 × 2 款"),
    ("目标品牌", "optional", "对标哪几个品牌，节点 01 检索策略要用"),
    ("备注",     "optional", ""),
]

REQUIRED = [name for name, level, _ in FIELDS if level == "required"]
KEY = [name for name, level, _ in FIELDS if level == "key"]
LEVELS = {name: level for name, level, _ in FIELDS}
HINTS = {name: hint for name, _, hint in FIELDS}


def blank_intake(tag="", requester=""):
    """tag 是同一天开多张单时用来区分的后缀。

    没有它，一位主管一天开两张单会拿到同一个 req_id，历史相似度检索会把
    两张不同的单当成同一张跳过。
    """
    suffix = f"-{tag.strip().upper()}" if tag.strip() else ""
    return {
        "meta": {
            "req_id": f"REQ-{date.today():%Y-%m%d}{suffix}",
            "project": "",
            "client": "",
            "requester": requester or "",
            "date": f"{date.today():%Y-%m-%d}",
        },
        "raw_request": "（陈主管原话，原样抄录，不要改写）",
        "fields": {name: "" for name, _, _ in FIELDS},
        "open_questions": [],
    }


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------


def is_filled(value):
    """空字符串、纯占位、"待定"一类都算没填。"""
    if not isinstance(value, str):
        return bool(value)
    text = value.strip()
    if not text:
        return False
    return not re.fullmatch(r"[（(]?(待定|待确认|TBD|tbd|\?+|-+|—+)[)）]?", text)


def validate(intake):
    """返回 (缺的必填, 缺的关键项)。"""
    fields = intake.get("fields", {})
    missing_required = [n for n in REQUIRED if not is_filled(fields.get(n))]
    missing_key = [n for n in KEY if not is_filled(fields.get(n))]
    return missing_required, missing_key


def auto_questions(missing_key):
    """缺失的关键字段自动变成待确认项。

    这些是机械的；语义层面的歧义（比如「Y2K」到底指哪一路）由填单的人
    或 intake 技能补进 open_questions，脚本不去猜。
    """
    return [f"「{name}」未填 —— 需要确认（例：{HINTS[name]}）" for name in missing_key]


# --------------------------------------------------------------------------
# 历史相似案例
# --------------------------------------------------------------------------

TOKEN = re.compile(r"[\w一-鿿]+")


def tokens(text):
    return set(TOKEN.findall(str(text or "").lower()))


def find_similar(intake, history_root, limit=3):
    """在历史需求单里找相似的。

    品类相同权重最高（同品类的历史方案最可能直接复用），其次是季节，
    再看其余字段的词重合度。
    """
    root = Path(history_root)
    if not root.exists():
        return []

    mine = intake.get("fields", {})
    my_tokens = tokens(" ".join(str(v) for v in mine.values()))
    scored = []

    for path in sorted(root.glob("*/00-brief/intake.json")):
        try:
            other = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue
        if other.get("meta", {}).get("req_id") == intake.get("meta", {}).get("req_id"):
            continue

        their = other.get("fields", {})
        score = 0.0
        if is_filled(their.get("品类")) and their.get("品类") == mine.get("品类"):
            score += 3
        if is_filled(their.get("季节")) and their.get("季节") == mine.get("季节"):
            score += 1.5
        overlap = my_tokens & tokens(" ".join(str(v) for v in their.values()))
        score += len(overlap) * 0.1

        if score > 0:
            scored.append((score, other, path))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:limit]


# --------------------------------------------------------------------------
# 确认单
# --------------------------------------------------------------------------


def render(intake, missing_required, questions, similar):
    meta = intake.get("meta", {})
    fields = intake.get("fields", {})
    out = []

    head = " · ".join(
        str(meta[k]) for k in ("req_id", "client", "project", "date") if meta.get(k)
    )
    out.append("=" * 60)
    out.append("需求确认单")
    out.append(head)
    out.append("=" * 60)

    raw = intake.get("raw_request", "").strip()
    if raw:
        out.append("\n【原始需求】")
        out.append(f"  {raw}")

    out.append("\n【我的理解】")
    for name, _, _ in FIELDS:
        value = fields.get(name, "")
        if is_filled(value):
            out.append(f"  {name}　{value}")
    filled = [n for n, _, _ in FIELDS if is_filled(fields.get(n))]
    if not filled:
        out.append("  （无 —— 需求单还没填）")

    if questions:
        out.append(f"\n【待确认 {len(questions)} 项】← 请陈主管逐条回复")
        for i, q in enumerate(questions, 1):
            out.append(f"  {i}. {q}")
    else:
        out.append("\n【待确认】无")

    if similar:
        out.append("\n【相似历史案例】")
        for score, other, path in similar:
            om = other.get("meta", {})
            of = other.get("fields", {})
            label = " · ".join(
                str(om[k]) for k in ("req_id", "client", "project") if om.get(k)
            )
            out.append(f"  {label}（相似度 {score:.1f}）")
            out.append(f"    品类 {of.get('品类', '—')}　季节 {of.get('季节', '—')}"
                       f"　廓形 {of.get('廓形', '—')}")
            adopted = other.get("adopted")
            if adopted:
                out.append(f"    当时采纳：{adopted}")
            out.append(f"    {path}")

    out.append("\n" + "-" * 60)
    if missing_required:
        out.append(f"状态：不能开工 —— 必填项缺失：{'、'.join(missing_required)}")
    elif questions:
        out.append(f"状态：可开工，但有 {len(questions)} 项待确认，"
                   "确认前不要进入节点 02 采集")
    else:
        out.append("状态：字段齐全，等陈主管签字后进入节点 01 检索策略")
    return "\n".join(out)


# --------------------------------------------------------------------------
# 转 brief.json
# --------------------------------------------------------------------------


def to_brief(intake, brief_path):
    """把需求单写进 brief.json 的 meta 与 brief 字段。

    brief.json 已有的其他字段（recommendations、inspiration…）原样保留，
    只覆盖这两块 —— 需求单是它们的上游，不该碰下游已经填好的内容。
    """
    path = Path(brief_path)
    brief = {}
    if path.exists():
        brief = json.loads(path.read_text(encoding="utf-8-sig"))

    meta = intake.get("meta", {})
    brief["meta"] = {
        "project": meta.get("project", ""),
        "client": meta.get("client", ""),
        "req_id": meta.get("req_id", ""),
        "date": meta.get("date", ""),
        "designer": "佘吉",
    }
    brief["brief"] = {
        name: intake["fields"][name]
        for name, _, _ in FIELDS
        if is_filled(intake.get("fields", {}).get(name))
    }

    path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="节点 00 · 需求接单与结构化")
    parser.add_argument("intake", nargs="?", help="需求单 JSON")
    parser.add_argument("--new", action="store_true", help="生成空白需求单")
    parser.add_argument("--tag", default="", help="req_id 后缀，同一天多张单靠它区分，例：SEED")
    parser.add_argument("--requester", default="", help="布置需求的主管姓名")
    parser.add_argument("-o", "--output", help="--new / --to-brief 的输出路径")
    parser.add_argument("--history", help="历史项目根目录，用于找相似案例")
    parser.add_argument("--to-brief", metavar="BRIEF", help="写入 brief.json")
    parser.add_argument("--fields", action="store_true", help="打印字段定义后退出")
    args = parser.parse_args()

    if args.fields:
        print(f"{'字段':<10}{'级别':<10}示例")
        for name, level, hint in FIELDS:
            print(f"{name:<10}{level:<10}{hint}")
        return

    if args.new:
        text = json.dumps(blank_intake(args.tag, args.requester),
                           ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"已生成空白需求单：{args.output}")
        else:
            print(text)
        return

    if not args.intake:
        parser.error("需要需求单 JSON，或用 --new 生成一份")

    intake = json.loads(Path(args.intake).read_text(encoding="utf-8-sig"))

    missing_required, missing_key = validate(intake)
    questions = list(intake.get("open_questions", [])) + auto_questions(missing_key)
    similar = find_similar(intake, args.history) if args.history else []

    print(render(intake, missing_required, questions, similar))

    if args.to_brief:
        if missing_required:
            sys.exit(f"\n必填项缺失，不写 brief.json：{'、'.join(missing_required)}")
        written = to_brief(intake, args.to_brief)
        print(f"\n已写入 {written}（meta 与 brief 字段；其余内容保留）")

    if missing_required:
        sys.exit(1)


if __name__ == "__main__":
    main()
