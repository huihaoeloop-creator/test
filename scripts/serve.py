#!/usr/bin/env python3
"""
本地部署看板。

数据不出这台机器：需求单、素材、台账全在 projects/ 下，页面从本机读。
主管们用浏览器打开就行，他们的电脑什么都不用装。

**这台机器不需要装 Claude。** 它只做两件事：扫 projects/ 出数据、把图发给
浏览器。跑佘吉（接单、出方案、采集、出 PPT）的那台才需要 Claude Code——
可以是同一台，也可以不是。

    python -X utf8 scripts/serve.py                      # 只有本机能开
    python -X utf8 scripts/serve.py --host 0.0.0.0       # 同一局域网的人能开
    python -X utf8 scripts/serve.py --host 0.0.0.0 --password 内部口令

用法要点：
  - --password 走 HTTP Basic，**明文传**。局域网内部挡一下自己人可以，
    绝不能把这个端口暴露到公网。真要外网访问，前面架 HTTPS 反代。
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

VERSION = "2026-09-08b"

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# 扫一次 projects/ 有开销，缓存几秒，别每次刷新都重扫
_cache = {"at": 0.0, "data": None}
_lock = threading.Lock()


def scan(projects, ttl=5.0):
    with _lock:
        if _cache["data"] is not None and time.monotonic() - _cache["at"] < ttl:
            return _cache["data"]
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(HERE / "board_data.py"), str(projects)],
            capture_output=True, text=True, encoding="utf-8",
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()[-800:], "requests": [],
                    "requesters": [], "nodes": [], "load": {}, "warnings": []}
        data = json.loads(result.stdout)
        _cache.update(at=time.monotonic(), data=data)
        return data


def images_of(task_dir, limit=60):
    """一张单收了哪些图。相对路径返回，前端拼 /img/ 取。"""
    assets = Path(task_dir) / "02-assets"
    if not assets.is_dir():
        return []
    out = []
    for path in sorted(assets.rglob("*")):
        if path.suffix.lower() in IMAGE_EXT and path.is_file():
            out.append({
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "direction": path.parent.parent.name,
                "channel": path.parent.name,
                "name": path.name,
            })
            if len(out) >= limit:
                break
    return out


def lan_address():
    """本机在局域网里的地址，用来打给主管们。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))     # 不真发包，只为拿本地网卡地址
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


class Handler(BaseHTTPRequestHandler):
    server_version = f"SheJiBoard/{VERSION}"
    projects = ROOT / "projects"
    board_html = ROOT / "board" / "sheji-board.html"
    state_file = ROOT / "board" / "state.local.json"
    password = None

    # ---------------- 基础 ----------------

    def log_message(self, fmt, *args):
        if not self.path.startswith("/img/"):        # 图片请求太吵，不记
            sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def fail(self, status, message):
        """错误也走 JSON。

        send_error 会把 message 塞进 HTTP 状态行，那一行只能是 latin-1——
        中文进去直接 UnicodeEncodeError，连接被打死，客户端看到的是"连不上"，
        而不是"被拒绝"。
        """
        self.send_json({"error": message}, status)

    def authorised(self):
        if not self.password:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                _, _, given = base64.b64decode(header[6:]).decode("utf-8").partition(":")
            except (ValueError, UnicodeDecodeError):
                given = ""
            if given == self.password:
                return True
        self.send_response(401)
        # realm 也只能是 ASCII —— 和状态行同一个坑，中文进去连接直接断，
        # 浏览器不会弹口令框，只显示"连不上"
        self.send_header("WWW-Authenticate", 'Basic realm="SheJi Board"')
        self.end_headers()
        return False

    # ---------------- 路由 ----------------

    def do_GET(self):
        if not self.authorised():
            return
        path = unquote(urlparse(self.path).path)

        if path in ("/", "/index.html"):
            return self.serve_board()
        if path == "/api/board":
            return self.send_json(scan(self.projects))
        if path == "/api/state":
            return self.send_json(self.read_state())
        if path.startswith("/api/images/"):
            return self.serve_image_list(path[len("/api/images/"):])
        if path.startswith("/img/"):
            return self.serve_file(path[len("/img/"):])
        self.fail(404, "没有这个地址")

    def do_POST(self):
        if not self.authorised():
            return
        if unquote(urlparse(self.path).path) != "/api/state":
            return self.fail(404, "没有这个地址")
        length = int(self.headers.get("Content-Length", 0))
        if length > 4 << 20:
            return self.send_json({"error": "内容太大"}, 413)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self.send_json({"error": "不是合法 JSON"}, 400)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        self.send_json({"ok": True})

    # ---------------- 各条路由的实现 ----------------

    def serve_board(self):
        if not self.board_html.exists():
            return self.fail(500, "找不到 board/sheji-board.html")
        html = self.board_html.read_text(encoding="utf-8")
        # 页面自己会去 fetch /api/board；这里补上 doctype 和 head，
        # 因为发布成 Artifact 时那层壳是平台加的，本地没有
        page = ("<!doctype html><html><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                "<style>body{margin:0;font:14px system-ui}img{max-width:100%}"
                "[hidden]{display:none!important}</style>"
                f"{html}</head></html>")
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def serve_image_list(self, req_id):
        for request in scan(self.projects).get("requests", []):
            if request["req_id"] == req_id:
                return self.send_json({"req_id": req_id,
                                       "images": images_of(ROOT / request["path"])})
        self.send_json({"req_id": req_id, "images": []})

    def serve_file(self, relative):
        # 只许读 projects/ 里的图。解析成绝对路径再比对，挡掉 ../ 那类
        target = (ROOT / relative).resolve()
        try:
            target.relative_to((ROOT / "projects").resolve())
        except ValueError:
            return self.fail(403, "只能读 projects/ 下的文件")
        if not target.is_file() or target.suffix.lower() not in IMAGE_EXT:
            return self.fail(404, "没有这个地址")
        blob = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "image/jpeg")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "max-age=300")
        self.end_headers()
        self.wfile.write(blob)

    def read_state(self):
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                pass
        return {"requests": {}}


def main():
    parser = argparse.ArgumentParser(description="本地部署佘吉工作台")
    parser.add_argument("--host", default="127.0.0.1",
                        help="默认只有本机能开；主管们要访问就填 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--projects", default=str(ROOT / "projects"))
    parser.add_argument("--password", help="HTTP Basic 口令（明文传，只在局域网用）")
    args = parser.parse_args()

    Handler.projects = Path(args.projects)
    Handler.password = args.password

    print(f"佘吉工作台　本地服务　版本 {VERSION}")
    print(f"  数据目录　{Handler.projects}")
    print(f"  本机打开　http://127.0.0.1:{args.port}/")
    if args.host == "0.0.0.0":
        print(f"  同事打开　http://{lan_address()}:{args.port}/")
        if not args.password:
            print("  ⚠ 没设 --password，同一局域网的人都能打开")
    if args.password:
        print("  ⚠ Basic 认证是明文传的。局域网内挡自己人可以，"
              "别把这个端口暴露到公网")
    print("  Ctrl+C 停")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停了")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
