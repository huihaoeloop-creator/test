#!/usr/bin/env python3
"""
节点 02 · 素材采集与出处台账。

按节点 01 的 plan.json 把图收进来，每一张都登记出处。

台账是这个节点真正的产出，不是图。原因有两条：交付 PPT 最后那页
Sources 要靠它生成；哪张图能进客户交付、哪张只能内部看，也全看它的
channel 字段。图丢了能重搜，出处丢了这张图就废了。

用法：
    # 从人工导出的目录收（WGSN、手动下载的品牌图都走这条）
    python -X utf8 collect.py --plan plan.json --ingest 导出/ \
        --channel wgsn --direction 1 --source "WGSN AW27 Knitwear Key Items"

    # 从团队自己的 Pinterest 画板拉（官方 API）
    set PINTEREST_ACCESS_TOKEN=...
    python -X utf8 collect.py --plan plan.json --pinterest-board 1234567890 --direction 2

    python -X utf8 collect.py --plan plan.json --status   # 进度对配额
    python -X utf8 collect.py --plan plan.json --check    # 台账与磁盘对账
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

VERSION = "2026-09-08c"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}

# 渠道 → 这张图能用到哪一步。节点 07 出交付页时要读这个字段。
USE = {
    "client":    "internal",   # 客户官网：客户现有款，是基准不是提案
    "brand":     "internal",   # 对标品牌官网：只能内部对标，不进客户交付页
    "wgsn":      "internal",   # 订阅站：同上，且不得外传
    "pinterest": "internal",   # 需保留作者署名
    "generated": "deliverable" # 节点 05 自己生成的，才是能交付的
}


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def slug(text):
    """「Elevated Everyday　精致基础」→ elevated-everyday"""
    ascii_part = re.sub(r"[^A-Za-z0-9]+", "-", text.split("　")[0]).strip("-").lower()
    return ascii_part or "direction"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# 方案与台账
# --------------------------------------------------------------------------


def load_plan(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not plan.get("approved"):
        sys.exit(
            f"plan.json 的 approved 还是 false —— 陈主管没确认检索式和配额，不开工。\n"
            f"  确认后把 {path} 里的 approved 改成 true 再跑。"
        )
    return plan


def direction_of(plan, ref):
    """--direction 收序号（1 起）或方向名的一段。"""
    directions = plan.get("directions", [])
    if not directions:
        return {"name": "General", "quota": plan.get("target", 0)}
    if str(ref).isdigit():
        index = int(ref) - 1
        if not 0 <= index < len(directions):
            sys.exit(f"没有第 {ref} 个方向，一共 {len(directions)} 个")
        return directions[index]
    for direction in directions:
        if str(ref).lower() in direction["name"].lower():
            return direction
    sys.exit(f"方向「{ref}」对不上，现有：" + "、".join(d["name"] for d in directions))


def ledger_path(root):
    return Path(root) / "ledger.json"


def load_ledger(root):
    path = ledger_path(root)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8-sig"))
    return {"version": VERSION, "items": []}


def save_ledger(root, ledger):
    ledger["updated"] = now()
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# 出处
# --------------------------------------------------------------------------


def read_sidecar(folder):
    """导出目录里可以放一份 sources.csv：filename,source_url,author,note

    有它就按文件逐张记出处；没有就退到 --source 那个批次级别的说明。
    """
    path = Path(folder) / "sources.csv"
    if not path.exists():
        return {}
    table = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("filename") or "").strip()
            if name:
                table[name] = {
                    "source_url": (row.get("source_url") or "").strip(),
                    "author": (row.get("author") or "").strip(),
                    "note": (row.get("note") or "").strip(),
                }
    return table


def provenance_level(entry):
    """出处够不够。不够的不拦，但 --check 会点名。"""
    if entry.get("source_url"):
        return "ok"
    if entry.get("source"):
        return "batch"      # 只有批次说明，落不到具体一张
    return "missing"


# --------------------------------------------------------------------------
# 采集
# --------------------------------------------------------------------------


def ingest_tree(root, plan, folder, channel, source, ledger):
    """收一棵 brand_collect.mjs 分好方向的目录树。

    子目录名是方向的 slug（elevated-everyday），对回 plan.json 里的方向名。
    对不上的子目录（比如 unmatched）照收，但方向留空并点名 —— 这些是商品名
    里没有线索、需要人工归类的，不能默默塞进某个方向。
    """
    tree = Path(folder)
    if not tree.is_dir():
        sys.exit(f"目录不存在：{folder}")

    by_slug = {slug(d["name"]): d for d in plan.get("directions", [])}
    total, orphan_dirs = 0, []

    for child in sorted(tree.iterdir()):
        if not child.is_dir():
            continue
        direction = by_slug.get(child.name)
        if direction is None:
            direction = {"name": f"（未归类·{child.name}）", "quota": 0}
            orphan_dirs.append(child.name)
        total += ingest(root, plan, child, channel, direction, source, ledger)

    if not total and not orphan_dirs:
        print("这棵树里没有子目录 —— 是不是该用 --ingest 而不是 --ingest-tree？")
    for name in orphan_dirs:
        print(f"  子目录「{name}」对不上任何方向，方向留空，需要人工归类")
    return total


def ingest(root, plan, folder, channel, direction, source, ledger):
    """把人工导出的目录收进来。按内容哈希去重，跨渠道也认。"""
    src = Path(folder)
    if not src.is_dir():
        sys.exit(f"目录不存在：{folder}")

    sidecar = read_sidecar(src)
    dest_dir = Path(root) / slug(direction["name"]) / channel
    dest_dir.mkdir(parents=True, exist_ok=True)

    known = {item["sha256"]: item for item in ledger["items"]}
    added, duplicate = 0, 0

    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXT:
            continue
        digest = sha256(path)
        if digest in known:
            duplicate += 1
            continue

        dest = dest_dir / f"{digest[:12]}{path.suffix.lower()}"
        shutil.copy2(path, dest)

        meta = sidecar.get(path.name, {})
        entry = {
            "id": digest[:12],
            "sha256": digest,
            "file": str(dest.relative_to(root)).replace("\\", "/"),
            "original_name": path.name,
            "channel": channel,
            "use": USE.get(channel, "internal"),
            "direction": direction["name"],
            "source": source or "",
            "source_url": meta.get("source_url", ""),
            "author": meta.get("author", ""),
            "note": meta.get("note", ""),
            "bytes": dest.stat().st_size,
            "collected_at": now(),
        }
        entry["provenance"] = provenance_level(entry)
        ledger["items"].append(entry)
        known[digest] = entry
        added += 1

    print(f"收入 {added} 张，跳过重复 {duplicate} 张 → {dest_dir}")
    if added and not sidecar:
        print("  没找到 sources.csv，出处只记到批次级别。"
              "逐张出处要在导出目录放一份 sources.csv（filename,source_url,author,note）")
    return added


def api_get(url, token):
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def pinterest_board(root, board_id, direction, ledger, limit):
    """从团队自己的画板拉图，走官方 v5 API。

    这里只能拉「本账号有权限的画板」——官方 API 没有开放全站搜索。所以
    Pinterest 那份配额的实际工作流是：人先在画板里选片，脚本再把画板拉下来。
    这既是合规要求，也是 API 的客观限制，不是绕不绕的问题。
    """
    token = os.environ.get("PINTEREST_ACCESS_TOKEN")
    if not token:
        sys.exit("没有 PINTEREST_ACCESS_TOKEN。去 developers.pinterest.com 建 app "
                 "拿 token，再 set PINTEREST_ACCESS_TOKEN=...")

    dest_dir = Path(root) / slug(direction["name"]) / "pinterest"
    dest_dir.mkdir(parents=True, exist_ok=True)
    known = {item["sha256"] for item in ledger["items"]}
    added, bookmark = 0, None

    while added < limit:
        url = f"https://api.pinterest.com/v5/boards/{board_id}/pins?page_size=25"
        if bookmark:
            url += f"&bookmark={urllib.parse.quote(bookmark)}"
        try:
            payload = api_get(url, token)
        except urllib.error.HTTPError as error:
            sys.exit(f"Pinterest API {error.code}：{error.read().decode('utf-8', 'replace')[:400]}")

        for pin in payload.get("items", []):
            if added >= limit:
                break
            image = ((pin.get("media") or {}).get("images") or {})
            best = image.get("1200x") or image.get("600x") or next(iter(image.values()), None)
            if not best or not best.get("url"):
                continue
            with urllib.request.urlopen(best["url"], timeout=30) as response:
                blob = response.read()
            digest = hashlib.sha256(blob).hexdigest()
            if digest in known:
                continue
            dest = dest_dir / f"{digest[:12]}.jpg"
            dest.write_bytes(blob)
            entry = {
                "id": digest[:12],
                "sha256": digest,
                "file": str(dest.relative_to(root)).replace("\\", "/"),
                "original_name": pin.get("id", ""),
                "channel": "pinterest",
                "use": USE["pinterest"],
                "direction": direction["name"],
                "source": f"Pinterest board {board_id}",
                "source_url": pin.get("link") or f"https://www.pinterest.com/pin/{pin.get('id')}/",
                "author": (pin.get("board_owner") or {}).get("username", ""),
                "note": (pin.get("title") or "")[:120],
                "bytes": len(blob),
                "collected_at": now(),
            }
            entry["provenance"] = provenance_level(entry)
            ledger["items"].append(entry)
            known.add(digest)
            added += 1

        bookmark = payload.get("bookmark")
        if not bookmark:
            break

    print(f"从画板 {board_id} 收入 {added} 张 → {dest_dir}")
    return added


# --------------------------------------------------------------------------
# 报表
# --------------------------------------------------------------------------


def status(plan, ledger):
    items = ledger["items"]
    print("=" * 62)
    print("采集进度　节点 02")
    print("=" * 62)

    directions = plan.get("directions", [])
    if directions:
        print("\n【按方向】")
        for direction in directions:
            got = [i for i in items if i["direction"] == direction["name"]]
            quota = direction.get("quota", 0)
            bar = "█" * min(20, int(20 * len(got) / quota)) if quota else ""
            print(f"  {direction['name']:<28}{len(got):>4}/{quota:<4}{bar}")

        # 方案外的方向（未归类、手工建的）也得露出来 —— 只按 plan 里的方向报，
        # 收进来但没归好类的图在这张表上是隐形的，谁也不会去处理它们
        planned = {d["name"] for d in directions}
        for name in sorted({i["direction"] for i in items} - planned):
            count = sum(1 for i in items if i["direction"] == name)
            print(f"  {name:<28}{count:>4}     ← 待人工归类")

    print("\n【按渠道】")
    for channel in plan.get("channels", []):
        got = [i for i in items if i["channel"] == channel["key"]]
        print(f"  {channel['name']:<24}{len(got):>4}/{channel.get('quota', 0):<4}"
              f"[{channel['mode']}]")
    other = sorted({i["channel"] for i in items} - {c["key"] for c in plan.get("channels", [])})
    for channel in other:
        got = [i for i in items if i["channel"] == channel]
        print(f"  {channel:<24}{len(got):>4}     （方案外）")

    print("\n【出处完整度】")
    for level, label in (("ok", "逐张有出处"), ("batch", "只有批次说明"), ("missing", "没有出处")):
        count = sum(1 for i in items if i.get("provenance") == level)
        print(f"  {label:<14}{count:>4}")

    internal = sum(1 for i in items if i.get("use") == "internal")
    print(f"\n合计 {len(items)} 张，其中 {internal} 张仅限内部参考，不得进客户交付页")


def check(root, ledger):
    """台账与磁盘对账。"""
    problems = []
    seen = set()
    for item in ledger["items"]:
        path = Path(root) / item["file"]
        if not path.exists():
            problems.append(f"台账有、磁盘没有：{item['file']}")
        seen.add(str(path.resolve()))
        if item.get("provenance") == "missing":
            problems.append(f"没有出处：{item['file']}（{item['channel']}）")

    for path in Path(root).rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_EXT:
            if str(path.resolve()) not in seen:
                problems.append(f"磁盘有、台账没有：{path.relative_to(root)}")

    batch_only = [i for i in ledger["items"] if i.get("provenance") == "batch"]

    if problems:
        print(f"发现 {len(problems)} 个问题：")
        for line in problems:
            print(f"  {line}")
        return 1

    print(f"对账通过：{len(ledger['items'])} 张，台账与磁盘一致，没有缺出处的")
    if batch_only:
        # 不是错，但 Sources 页只能写到「某份报告」，落不到具体哪一张
        print(f"提醒：{len(batch_only)} 张只有批次级出处，Sources 页写不出单张来源。"
              f"补法是在导出目录放 sources.csv 后重收")
    return 0


# --------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="节点 02 · 素材采集与出处台账")
    parser.add_argument("--plan", required=True, help="节点 01 的 plan.json")
    parser.add_argument("--root", help="素材根目录，默认 plan.json 同级的 ../02-assets")
    parser.add_argument("--ingest", metavar="DIR", help="从人工导出的目录收图")
    parser.add_argument("--ingest-tree", metavar="DIR",
                        help="收一棵按方向分好的目录树，子目录名对回方向")
    parser.add_argument("--channel", default="wgsn", help="来源渠道（brand/wgsn/pinterest）")
    parser.add_argument("--direction", help="方向序号或名字的一段")
    parser.add_argument("--source", help="批次级出处说明，例：WGSN AW27 Knitwear Key Items")
    parser.add_argument("--pinterest-board", metavar="ID", help="从画板拉图（官方 API）")
    parser.add_argument("--limit", type=int, help="本次最多收多少张，默认吃满该方向配额")
    parser.add_argument("--status", action="store_true", help="进度对配额")
    parser.add_argument("--check", action="store_true", help="台账与磁盘对账")
    args = parser.parse_args()

    print(f"collect 版本 {VERSION}", file=sys.stderr)

    plan_path = Path(args.plan)
    plan = load_plan(plan_path)
    root = Path(args.root) if args.root else plan_path.parent.parent / "02-assets"
    root.mkdir(parents=True, exist_ok=True)
    ledger = load_ledger(root)

    did_work = False

    if args.ingest_tree:
        ingest_tree(root, plan, args.ingest_tree, args.channel, args.source, ledger)
        save_ledger(root, ledger)
        did_work = True

    if args.ingest or args.pinterest_board:
        if not args.direction:
            sys.exit("要指定 --direction（序号或方向名的一段），图得先分好组")
        direction = direction_of(plan, args.direction)
        limit = args.limit or direction.get("quota", 40)

        if args.ingest:
            ingest(root, plan, args.ingest, args.channel, direction, args.source, ledger)
        if args.pinterest_board:
            pinterest_board(root, args.pinterest_board, direction, ledger, limit)

        save_ledger(root, ledger)
        did_work = True

    if args.status or not did_work and not args.check:
        status(plan, ledger)
    if args.check:
        sys.exit(check(root, ledger))


if __name__ == "__main__":
    main()
