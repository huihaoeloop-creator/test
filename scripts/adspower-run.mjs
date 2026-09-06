#!/usr/bin/env node
/**
 * Drive an AdsPower profile with Playwright over CDP.
 *
 * Usage:
 *   ADSPOWER_KEY=... ADSPOWER_USER_ID=... node scripts/adspower-run.mjs <url> [--out out/shot.png] [--stop]
 */
import { mkdir } from 'node:fs/promises';
import { dirname } from 'node:path';
import { parseArgs } from 'node:util';
import { connectProfile, disconnect, stopProfile } from './lib/adspower.mjs';

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    out: { type: 'string' },
    profile: { type: 'string' },
    stop: { type: 'boolean', default: false },
  },
});

const url = positionals[0];
if (!url) {
  console.error('usage: node scripts/adspower-run.mjs <url> [--profile ID] [--out path] [--stop]');
  process.exit(1);
}

const userId = values.profile ?? process.env.ADSPOWER_USER_ID;
const { browser, page } = await connectProfile(userId);

try {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  console.log(`${page.url()}  ${await page.title()}`);

  if (values.out) {
    await mkdir(dirname(values.out), { recursive: true });
    await page.screenshot({ path: values.out, fullPage: true });
    console.log(`screenshot: ${values.out}`);
  }
} finally {
  await disconnect(browser);
  // Only close the AdsPower window when asked — leaving it open is usually
  // what you want between scripted steps on the same profile.
  if (values.stop) await stopProfile(userId);
}
