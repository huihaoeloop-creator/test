# Headless browser automation with Playwright

A minimal, working Playwright setup: a headless-only config, a local fixture app
so the suite needs no network, and a standalone script for one-off automation
that isn't a test.

## Requirements

- Node.js 18+
- `@playwright/test` is pinned to **1.56.1** on purpose — it must match the
  Chromium build already present on the machine. Bumping it means downloading a
  new browser build (`npx playwright install chromium`).

```bash
npm install
```

If Chromium is pre-installed somewhere non-standard, point Playwright at it:

```bash
export PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1   # stops npm postinstall re-fetching
```

## Run the tests

```bash
npm test              # headless (the default)
npm run test:headed   # needs a display server
npm run test:ui       # interactive runner
npm run report        # open the last HTML report
npm run typecheck     # tsc --noEmit over the config and specs
```

`playwright.config.ts` starts `http-server` on `./fixtures` (port 4173, override
with `PORT`) and points `baseURL` at it. Set `BASE_URL` to test your own dev
server instead, and the fixture server is skipped:

```bash
BASE_URL=http://localhost:3000 npm test
```

## Standalone automation

`scripts/scrape.mjs` drives a page without the test runner — load a URL, collect
console errors and failed requests, extract the title and links, optionally
screenshot:

```bash
node scripts/scrape.mjs http://127.0.0.1:4173/ --json
node scripts/scrape.mjs https://example.com --out out/page.png
```

Flags: `--json` (machine-readable output), `--out <path>` (full-page
screenshot), `--timeout <ms>` (navigation timeout, default 30000).

## Proxy

`scripts/lib/env.mjs` reads `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` (or
`PLAYWRIGHT_PROXY` to override) and passes them to Chromium explicitly, so
routing behaves the same on every platform instead of relying on Chromium's own
env parsing. Both the test config and the script use it.

Loopback and private-network targets are never proxied: Chromium's implicit
localhost bypass does not survive an explicit `--proxy-server`, and
`--proxy-bypass-list` does not restore it, so the proxy is dropped at launch
time when the target is local. Without this a dev server on `127.0.0.1` gets
routed to the proxy and comes back `405`.

## Driving an AdsPower profile

AdsPower is not one of the browsers Claude Code's `--chrome` integration
supports, and it launches each profile in its own throwaway user-data-dir, so
the extension route does not apply. Attach over CDP instead: AdsPower's Local
API starts a profile and returns a DevTools websocket endpoint that Playwright
connects to with `chromium.connectOverCDP()`.

Enable the Local API in the AdsPower client, then:

```bash
export ADSPOWER_API=http://local.adspower.net:50325   # port is configurable
export ADSPOWER_KEY=<api key from the client>
export ADSPOWER_USER_ID=<profile id>

node scripts/adspower-run.mjs https://example.com --out out/shot.png
```

`scripts/lib/adspower.mjs` wraps `browser/start`, `browser/stop` and
`browser/active`, throttles calls (the Local API is rate limited), and attaches
to the profile's **existing** context — a fresh context would not carry the
profile's cookies or fingerprint. Disconnecting leaves the window open;
`--stop` closes the profile through the API.

Field names follow the Local API docs and have changed between releases —
check yours: https://localapi-doc-en.adspower.com/

AdsPower also ships an official LocalAPI MCP server, which plugs into Claude
Code directly (`claude mcp add`) if you would rather have Claude call it than
run these scripts: https://help.adspower.com/docs/MCP

## Running inside a sandboxed CI/agent container

Local automation works fully — that covers testing your own app, which is the
common case. Reaching the public internet may not, for reasons outside this
repo:

- **Policy denial** — `ERR_TUNNEL_CONNECTION_FAILED`, with the egress gateway
  answering 403 to `CONNECT`. The host is not on the environment's allowlist.
- **TLS interception** — `ERR_CERT_AUTHORITY_INVALID`. The proxy re-terminates
  TLS with a private CA. Chromium does not read the system OpenSSL bundle, so
  it rejects the substituted certificate even though `curl` and Node accept it.
- **Tunnel resets** — `ERR_CONNECTION_RESET` mid-handshake on some hosts.

Prefer local fixtures or a dev server started by `webServer` for anything that
has to run in such an environment.

## Layout

```
fixtures/            static page the suite drives (a small login form)
playwright.config.ts headless config, fixture web server, chromium project
scripts/scrape.mjs   standalone automation entry point
scripts/lib/env.mjs  shared proxy-from-environment helper
tests/               specs
```

## Design workflow (佘吉)

Scripts for the design-request pipeline, one per workflow node. Each writes a
JSON artefact the next node reads, and each stops for human review — nothing
advances a node on its own.

| Node | Script | In → Out |
|---|---|---|
| 00 需求接单 | `scripts/intake.py` | 主管的一句话 → `intake.json` / `brief.json` |
| 01 检索策略 | `scripts/search_plan.py` | `intake.json` → `plan.json` |
| 02 素材采集 | `scripts/collect.py`, `scripts/brand_collect.mjs` | `plan.json` + 图 → `ledger.json` + 分方向素材目录 |
| 03 风格解构 | `scripts/decompose.py` | `ledger.json` + 图 → `style.json`（配色、词频算出来；廓形克重留白） |
| 04 出图指令 | `scripts/prompts.py` | `style.json` → `prompts.json` + 各工具的 prompt 文件 |
| — 看板数据 | `scripts/board_data.py` | `projects/` → `board.json` |
| — 本地部署 | `scripts/serve.py` | 本机起服务，主管用浏览器看 |
| 06 后期处理 | `scripts/postprocess.py` | 生成图 → 去背白底图 + `brief.json` 的 recommendations |
| 07 出 PPT | `scripts/build_deck.py` | `brief.json` + `.potx` 模版 → 交付 PPT |
| — 诊断 | `scripts/check_pptx.py`, `scripts/dump_template.py` | 查 PPT 结构 / 看模版排版 |

Projects are laid out three deep — supervisor, then task, then node:
`projects/<主管名>/<req_id>/02-assets/<方向>/<渠道>/`. A supervisor hands over a
zip whose folder names are the channels; the images are renamed on the way in.
What goes back to them is image files, not links.

```bash
python -X utf8 scripts/intake.py intake.json --to-brief brief.json
python -X utf8 scripts/search_plan.py intake.json --target 40 -o plan.json
python -X utf8 scripts/collect.py --plan plan.json --ingest 导出/ --channel wgsn --direction 1
python -X utf8 scripts/build_deck.py brief.json -t 模版.potx -o 交付.pptx
```

Channel compliance is declared in `search_plan.py`'s `CHANNELS` table, not
decided at run time: brand sites are internal reference only and must stay
credited on the Sources page, Pinterest goes through the official API,
WGSN / Fashion Snoops produce a pick list for a licensed human to export, and
Instagram is not collected at all.

### 看板

`board/sheji-board.html` is the published board — every supervisor's requests,
where each one sits in the nine nodes, the review gates and the channel rules.
Its numbers come from `board_data.py`, which walks `projects/` and decides each
request's position from **which artefacts exist**, not from a status field
someone maintains by hand. Refreshing the board is: run the scanner, rebuild the
page, republish to the same URL.

Gate sign-offs and notes are per request and live in the artifact's own shared
store, so several people see the same state and a republish does not clear it.
The page cannot read anyone's `ledger.json`, so its collection figures are a
snapshot taken when it was published.

One supervisor can have several requests open at once, so the board sorts by
deadline with overdue first, and each supervisor's chip carries the counts that
actually need acting on — how many are waiting on them, how many have slipped.
The scanner also reports duplicate `req_id`s and requests with no requester.

### Running it locally

```bash
python -X utf8 scripts/serve.py --host 0.0.0.0 --password <口令>
```

Serves the same `board/sheji-board.html` off this machine, and the page notices:
it fetches live data from `/api/board` instead of using its embedded snapshot,
shows thumbnails of what has actually been collected, and keeps gate sign-offs
in `board/state.local.json`. Nothing leaves the machine.

**The machine serving this does not need Claude installed** — only Python, from
the standard library. Claude Code is needed on whichever machine runs 佘吉
(intake, search plan, collection, deck), which may or may not be the same one.
Supervisors need nothing but a browser.

`--password` is HTTP Basic over plain HTTP: it keeps colleagues on the LAN from
wandering in, and is not a security boundary. Do not expose the port to the
internet; put a TLS reverse proxy in front if it has to be reachable remotely.

The supervisor filter is a filter, not isolation — everyone sees every request.
Real per-supervisor separation needs viewer identity, which this account's
artifact runtime does not offer.
