#!/usr/bin/env node
/**
 * Standalone headless automation example — no test runner involved.
 *
 * Usage:
 *   node scripts/scrape.mjs <url> [--out out/page.png] [--json]
 *
 * Loads a page, collects console errors and failed requests, extracts the
 * title plus every link, and optionally writes a full-page screenshot.
 */
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import { parseArgs } from 'node:util';
import { chromium } from '@playwright/test';
import { proxyFromEnv } from './lib/env.mjs';

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    out: { type: 'string' },
    json: { type: 'boolean', default: false },
    timeout: { type: 'string', default: '30000' },
  },
});

const url = positionals[0];
if (!url) {
  console.error('usage: node scripts/scrape.mjs <url> [--out out/page.png] [--json]');
  process.exit(1);
}

const browser = await chromium.launch({ proxy: proxyFromEnv(url) });
const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const page = await context.newPage();

const consoleErrors = [];
const failedRequests = [];
page.on('console', (msg) => {
  if (msg.type() === 'error') consoleErrors.push(msg.text());
});
page.on('requestfailed', (req) => {
  failedRequests.push({ url: req.url(), error: req.failure()?.errorText ?? 'unknown' });
});

try {
  const response = await page.goto(url, {
    waitUntil: 'domcontentloaded',
    timeout: Number(values.timeout),
  });

  const result = {
    url: page.url(),
    status: response?.status() ?? null,
    title: await page.title(),
    links: await page.$$eval('a[href]', (as) =>
      as.map((a) => ({ text: a.textContent?.trim() ?? '', href: a.href })),
    ),
    consoleErrors,
    failedRequests,
  };

  if (values.out) {
    await mkdir(dirname(values.out), { recursive: true });
    await page.screenshot({ path: values.out, fullPage: true });
    result.screenshot = values.out;
  }

  if (values.json) {
    console.log(JSON.stringify(result, null, 2));
  } else {
    console.log(`${result.status}  ${result.title}`);
    console.log(`${result.links.length} link(s)`);
    if (consoleErrors.length) console.log(`console errors: ${consoleErrors.join(' | ')}`);
    if (failedRequests.length) console.log(`failed requests: ${failedRequests.length}`);
    if (result.screenshot) console.log(`screenshot: ${result.screenshot}`);
  }
} finally {
  await context.close();
  await browser.close();
}
