#!/usr/bin/env python3
"""
节点 06 · 后期处理。

把生成图（或人工搜来的图）抠掉背景、统一尺寸、按节点 07 要的命名规则落盘，
顺便产出 recommendations 骨架，直接喂给 build_deck.py。

抠背景只用 Pillow，不依赖 rembg / OpenCV：

  这条流程的图是白底棚拍（节点 04 的 prompt 里写死了 plain seamless white
  studio background），背景均匀且连通到画面边缘。这种图用「从边缘漫填」比
  跑一个分割模型更准，也不用下 170MB 权重。

  代价是：**背景不均匀的图它做不了**（渐变底、场景图、街拍）。所以每张都
  报去掉了多少像素，去太少或去太多都点名 —— 不会默默输出一张废图。

用法：
    python -X utf8 postprocess.py --in 生成图/ --out 06-clean/ --into brief.json
    python -X utf8 postprocess.py --in 生成图/ --out 06-clean/ --tolerance 24
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VERSION = "2026-09-08a"

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
except ImportError:
    sys.exit("需要 Pillow：pip install Pillow")

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def pixels_of(image):
    """Pillow 14 要移除 getdata()，新名字是 get_flattened_data()。
    两个都试，装的是哪个版本都能跑。"""
    getter = getattr(image, "get_flattened_data", None) or image.getdata
    return list(getter())

# 文件名里认这些当正/反面。生图工具导出的名字五花八门，先认标记再退回排序配对。
FRONT = re.compile(r"(?:^|[^a-z])(front|f)(?:[^a-z]|$)|正面?|前", re.I)
BACK = re.compile(r"(?:^|[^a-z])(back|b|rear)(?:[^a-z]|$)|背面?|反|后", re.I)


# --------------------------------------------------------------------------
# 抠背景
# --------------------------------------------------------------------------


def background_mask(image, tolerance, sample=600):
    """哪些像素是背景。

    两步，缺一不可：
      1. 跟边缘取到的背景色比，颜色接近的算"像背景"
      2. 只保留**连通到画面边缘**的那部分

    第 2 步是关键。白毛衣身上的白色和背景色一样接近，但它不连到边缘，
    所以留得住 —— 只做第 1 步会把白衣服一起抠掉。
    """
    small = image.copy()
    small.thumbnail((sample, sample), Image.LANCZOS)
    width, height = small.size

    # 背景色取四条边的中位数，比只取一个角稳
    pixels = pixels_of(small)
    border = ([pixels[x] for x in range(width)]
              + [pixels[(height - 1) * width + x] for x in range(width)]
              + [pixels[y * width] for y in range(height)]
              + [pixels[y * width + width - 1] for y in range(height)])
    channels = [sorted(c[i] for c in border)[len(border) // 2] for i in range(3)]
    base = tuple(channels)

    # 逐通道差的最大值——转灰度会平均掉，红底上的绿衣服就分不出来了
    diff = ImageChops.difference(small, Image.new("RGB", small.size, base))
    red, green, blue = diff.split()
    distance = ImageChops.lighter(ImageChops.lighter(red, green), blue)

    # 255 = 像背景
    flat = distance.point(lambda v: 255 if v <= tolerance else 0)

    # 从边缘漫填，只留连通到边缘的那片
    marker = 128
    drawer = ImageDraw
    for x in range(0, width, max(1, width // 24)):
        for y in (0, height - 1):
            if flat.getpixel((x, y)) == 255:
                drawer.floodfill(flat, (x, y), marker)
    for y in range(0, height, max(1, height // 24)):
        for x in (0, width - 1):
            if flat.getpixel((x, y)) == 255:
                drawer.floodfill(flat, (x, y), marker)

    mask = flat.point(lambda v: 255 if v == marker else 0)
    removed = sum(pixels_of(mask)) / (255 * width * height)

    # 画面四边有多少被判成了背景。均匀白底应该接近 100%；明显低于这个数
    # 说明底不均匀（渐变、场景、光斑），抠完边上还留着一圈 ——
    # 光看"去掉了百分之几"是发现不了的：渐变底能去掉 54%，看着很正常。
    edge = ([mask.getpixel((x, 0)) for x in range(width)]
            + [mask.getpixel((x, height - 1)) for x in range(width)]
            + [mask.getpixel((0, y)) for y in range(height)]
            + [mask.getpixel((width - 1, y)) for y in range(height)])
    edge_clean = sum(1 for v in edge if v > 200) / len(edge)

    mask = mask.filter(ImageFilter.GaussianBlur(1.2))       # 边缘羽化，不然锯齿很硬
    return mask.resize(image.size, Image.LANCZOS), removed, base, edge_clean


def cut_out(path, tolerance, canvas=None):
    """返回 (白底 RGB 图, 带 alpha 的 RGBA 图, 去掉的比例, 背景色)。"""
    with Image.open(path) as raw:
        image = raw.convert("RGB")

    mask, removed, base, edge_clean = background_mask(image, tolerance)
    alpha = mask.point(lambda v: 255 - v)
    cut = image.copy()
    cut.putalpha(alpha)

    white = Image.new("RGB", image.size, (255, 255, 255))
    white.paste(image, mask=alpha)

    if canvas:
        white = fit_canvas(white, canvas)
        cut = fit_canvas(cut, canvas, transparent=True)
    return white, cut, removed, base, edge_clean


def fit_canvas(image, ratio, transparent=False):
    """补边到统一画幅。

    交付页两张图并排，画幅不一致的话一张顶天一张缩在中间，很难看。
    补边而不是裁切 —— 裁切会切掉下摆或肩线，那是判断款式的关键部位。
    """
    want_w, want_h = ratio
    width, height = image.size
    target = width / height
    if abs(target - want_w / want_h) < 0.01:
        return image
    if target > want_w / want_h:
        new = (width, round(width * want_h / want_w))
    else:
        new = (round(height * want_w / want_h), height)
    fill = (255, 255, 255, 0) if transparent else (255, 255, 255)
    canvas = Image.new("RGBA" if transparent else "RGB", new, fill)
    canvas.paste(image, ((new[0] - width) // 2, (new[1] - height) // 2),
                 image if transparent else None)
    return canvas


# --------------------------------------------------------------------------
# 配对
# --------------------------------------------------------------------------


def pair_up(files):
    """把文件配成 (正, 反)。

    先按文件名里的正反标记配；一个标记都没有才退回「排序后两两配」，
    并且明说这是猜的 —— 配错了整页正反颠倒，是很难在 PPT 里一眼看出来的错。
    """
    marked = [(p, bool(FRONT.search(p.stem)), bool(BACK.search(p.stem))) for p in files]
    if not any(front or back for _, front, back in marked):
        pairs = [(files[i], files[i + 1] if i + 1 < len(files) else None)
                 for i in range(0, len(files), 2)]
        return pairs, "文件名里没有正反标记，按排序两两配的 —— 核一眼"

    fronts = [p for p, front, back in marked if front and not back]
    backs = [p for p, front, back in marked if back and not front]
    rest = [p for p, front, back in marked if front == back]

    def key(path):
        # 去掉正反标记后的名字，用来把同一款的正反凑到一起
        return FRONT.sub("", BACK.sub("", path.stem)).strip("_- ").lower()

    by_key = {}
    for path in backs:
        by_key.setdefault(key(path), []).append(path)

    pairs, unmatched = [], []
    for front in fronts:
        candidates = by_key.get(key(front))
        pairs.append((front, candidates.pop(0) if candidates else None))
        if not candidates:
            by_key.pop(key(front), None)
    for leftovers in by_key.values():
        unmatched.extend(leftovers)
    unmatched.extend(rest)

    warning = ""
    if unmatched:
        warning = (f"{len(unmatched)} 张配不上对：" +
                   "、".join(p.name for p in unmatched[:5]) +
                   "　—— 正反必须成对，缺的那张要补生")
    return pairs, warning


# --------------------------------------------------------------------------


def run(source, out, tolerance, canvas, keep_alpha, title):
    files = sorted((p for p in Path(source).iterdir()
                    if p.is_file() and p.suffix.lower() in IMAGE_EXT),
                   key=lambda p: p.name.lower())
    if not files:
        sys.exit(f"{source} 里没有图")

    pairs, warning = pair_up(files)
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)

    rows, problems = [], []
    for index, (front, back) in enumerate(pairs, 1):
        style = f"{index:03d}"
        entry = {"style": style, "title": title, "front": "", "back": "", "notes": []}

        for view, path, order in (("front", front, 1), ("back", back, 2)):
            if path is None:
                problems.append(f"{style} 缺{'正' if view == 'front' else '反'}面")
                continue
            white, cut, removed, base, edge_clean = cut_out(path, tolerance, canvas)
            # 名字里带顺序号，节点 07 的 --pair 靠排序配对时才不会正反颠倒
            name = f"{style}_{order}_{view}.jpg"
            white.save(target / name, "JPEG", quality=92, optimize=True, progressive=False)
            if keep_alpha:
                cut.save(target / f"{style}_{order}_{view}.png", "PNG")
            entry[view] = (target / name).as_posix()

            if edge_clean < 0.85:
                problems.append(f"{name} 画面四边只有 {edge_clean:.0%} 被判成背景 —— "
                                f"底不均匀（渐变/场景/光斑），抠完边上还留着一圈。"
                                f"这种图得换白底重生，调容差没用")
            if removed < 0.03:
                problems.append(f"{name} 只去掉 {removed:.0%} —— 背景可能不均匀，"
                                f"抠不动（底色取到 RGB{base}）")
            elif removed > 0.90:
                problems.append(f"{name} 去掉了 {removed:.0%} —— 可能把衣服一起抠了，"
                                f"调低 --tolerance 再试")
        rows.append(entry)

    return rows, problems, warning


def brief_rows_relative(rows, base):
    """把 recommendations 里的绝对路径改成相对 brief.json 的路径。"""
    for entry in rows:
        for view in ("front", "back"):
            if not entry[view]:
                continue
            try:
                entry[view] = Path(entry[view]).resolve().relative_to(base).as_posix()
            except ValueError:
                pass          # 不在 brief 同一棵树下，保持原样
        yield entry


def main():
    parser = argparse.ArgumentParser(description="节点 06 · 后期处理（抠背景 + 命名）")
    parser.add_argument("--in", dest="source", required=True, help="生成图目录")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--tolerance", type=int, default=18,
                        help="背景色容差，默认 18。去不干净调大，吃掉衣服调小")
    parser.add_argument("--canvas", default="3:4", help="统一画幅，默认 3:4；none 表示不补边")
    parser.add_argument("--alpha", action="store_true", help="同时导一份透明底 PNG")
    parser.add_argument("--title", default="Knitwear Recommendation", help="交付页标题")
    parser.add_argument("--into", metavar="BRIEF", help="把 recommendations 写进 brief.json")
    args = parser.parse_args()

    print(f"postprocess 版本 {VERSION}", file=sys.stderr)

    canvas = None
    if args.canvas.lower() != "none":
        want = args.canvas.replace("：", ":").split(":")
        canvas = (int(want[0]), int(want[1]))

    rows, problems, warning = run(args.source, args.out, args.tolerance,
                                  canvas, args.alpha, args.title)

    print("=" * 62)
    print("后期处理　节点 06")
    print("=" * 62)
    print(f"\n{len(rows)} 款 → {args.out}")
    if warning:
        print(f"  ⚠ {warning}")
    for entry in rows:
        state = "正反齐" if entry["front"] and entry["back"] else "缺一张"
        print(f"  {entry['style']}　{state}")

    if problems:
        print(f"\n【要看一眼的 {len(problems)} 处】")
        for line in problems:
            print(f"  {line}")

    if args.into:
        path = Path(args.into)
        brief = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
        # 路径写成相对 brief.json 的位置：整个项目目录拷到别的机器上还能用，
        # 绝对路径换台机器就全废了
        base = path.resolve().parent
        for entry in brief_rows_relative(rows, base):
            pass
        brief["recommendations"] = rows
        path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写进 {path} 的 recommendations（{len(rows)} 款），"
              "其余字段原样保留")
        print("下一步：python -X utf8 scripts/build_deck.py "
              f"{path} -t 模版.potx -o 交付.pptx")

    print("\n" + "-" * 62)
    if problems or warning:
        print("状态：有要人看的地方，核完再进节点 07")
    else:
        print("状态：都干净，可以进节点 07 出 PPT")


if __name__ == "__main__":
    main()
