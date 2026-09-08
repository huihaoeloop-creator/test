#!/usr/bin/env python3
"""
扫描 projects/，汇总每张需求单当前走到哪个节点，产出看板要的 board.json。

看板不能读你们机器上的文件，所以每次刷新看板都是「跑这个脚本 → 把结果
贴进页面 → 重新发布」。脚本存在的意义是：那些数字不该由人手抄。

判定当前节点靠**产物是否存在**，不靠谁在哪填了个状态字段——状态字段会
和现实脱节，产物不会。

用法：
    python -X utf8 board_data.py projects/ -o board.json
    python -X utf8 board_data.py projects/            # 打到屏幕上
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

VERSION = "2026-09-08a"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intake import REQUIRED, is_filled          # noqa: E402  单一事实来源，别抄第二份

# 节点在这里只需要「叫什么」和「归谁管」，跑法在各自的脚本里
NODES = [
    ("00", "需求接单", "intake.py"),
    ("01", "检索策略", "search_plan.py"),
    ("02", "素材采集", "collect.py"),
    ("03", "风格解构", None),
    ("04", "出图指令", None),
    ("05", "批量生成", None),
    ("06", "后期处理", None),
    ("07", "出 PPT", "build_deck.py"),
    ("08", "复盘沉淀", None),
]


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def where_is_it(intake, plan, ledger, requester):
    """当前卡在哪个节点，以及卡的原因。

    返回 (节点号, 状态, 一句话)。状态用三档：
      running 在跑　gate 等人点头　blocked 缺东西没法往下
    """
    fields = intake.get("fields", {})
    missing = [n for n in REQUIRED if not is_filled(fields.get(n))]
    if missing:
        return "00", "blocked", f"必填项缺失：{'、'.join(missing)}"

    questions = intake.get("open_questions", [])
    if questions:
        return "00", "gate", f"{len(questions)} 项待{requester}确认"

    if plan is None:
        return "01", "running", "需求单已齐，等出检索方案"
    if not plan.get("approved"):
        return "01", "gate", "检索式与配额待确认（plan.json approved=false）"

    target = plan.get("target", 0)
    got = len(ledger.get("items", [])) if ledger else 0
    if got < target:
        return "02", "running", f"采集中 {got}/{target} 张"

    return "03", "blocked", "节点 03 风格解构还没建"


def per_direction(plan, ledger):
    items = (ledger or {}).get("items", [])
    out = []
    for direction in plan.get("directions", []) if plan else []:
        got = sum(1 for i in items if i.get("direction") == direction["name"])
        out.append({
            "name": direction["name"],
            "terms": direction.get("terms", []),
            "quota": direction.get("quota", 0),
            "got": got,
        })
    return out


def scan_one(folder):
    intake = read_json(folder / "00-brief" / "intake.json")
    if intake is None:
        return None
    plan = read_json(folder / "01-search" / "plan.json")
    ledger = read_json(folder / "02-assets" / "ledger.json")

    meta = intake.get("meta", {})
    requester = meta.get("requester", "主管")
    node, status, why = where_is_it(intake, plan, ledger, requester)
    fields = intake.get("fields", {})
    items = (ledger or {}).get("items", [])

    return {
        "req_id": meta.get("req_id", folder.name),
        "project": meta.get("project", ""),
        "client": meta.get("client", ""),
        "requester": requester,
        "date": meta.get("date", ""),
        "example": bool(intake.get("example")),
        "raw_request": intake.get("raw_request", ""),
        "fields": {k: v for k, v in fields.items() if is_filled(v)},
        "open_questions": intake.get("open_questions", []),
        "node": node,
        "status": status,
        "why": why,
        "target": (plan or {}).get("target", 0),
        "collected": len(items),
        "internal_only": sum(1 for i in items if i.get("use") == "internal"),
        "directions": per_direction(plan, ledger),
        "directions_status": intake.get("directions_status", ""),
        "channels": [
            {k: c.get(k) for k in ("name", "mode", "quota", "license")}
            for c in (plan or {}).get("channels", [])
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="扫描 projects/，产出看板数据")
    parser.add_argument("root", help="项目根目录，例：projects/")
    parser.add_argument("-o", "--output", help="写出 board.json")
    args = parser.parse_args()

    print(f"board_data 版本 {VERSION}", file=sys.stderr)

    requests = []
    for folder in sorted(Path(args.root).iterdir()):
        if folder.is_dir():
            one = scan_one(folder)
            if one:
                requests.append(one)

    by_requester = {}
    for request in requests:
        by_requester.setdefault(request["requester"], []).append(request["req_id"])

    board = {
        "generated_by": f"board_data.py {VERSION}",
        "nodes": [{"n": n, "name": name, "script": script} for n, name, script in NODES],
        "built": [n for n, _, script in NODES if script],
        "requesters": sorted(by_requester),
        "requests": requests,
    }

    text = json.dumps(board, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"已写出 {args.output}：{len(requests)} 张单，"
              f"{len(by_requester)} 位主管（{'、'.join(sorted(by_requester))}）")
    else:
        print(text)


if __name__ == "__main__":
    main()
