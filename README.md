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
