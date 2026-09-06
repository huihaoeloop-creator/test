/**
 * AdsPower Local API client.
 *
 * AdsPower starts a profile as a normal Chromium process and hands back a CDP
 * endpoint, so Playwright attaches over CDP rather than launching a browser of
 * its own. Enable the Local API in the AdsPower client first and copy the API
 * key from there.
 *
 *   ADSPOWER_API      default http://local.adspower.net:50325
 *   ADSPOWER_KEY      API key from the client (omit if your version predates it)
 *   ADSPOWER_USER_ID  profile id to drive
 *
 * Field names follow the Local API docs — verify them against the version you
 * run, they have changed across releases:
 * https://localapi-doc-en.adspower.com/
 */
import { chromium } from '@playwright/test';

const BASE = process.env.ADSPOWER_API ?? 'http://local.adspower.net:50325';

// The Local API is rate limited (roughly one request per second). Serialise
// calls so a loop over many profiles does not trip it.
let lastCall = 0;
async function throttle() {
  const wait = 1100 - (Date.now() - lastCall);
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
  lastCall = Date.now();
}

async function call(path, params = {}) {
  await throttle();

  const url = new URL(path, BASE);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }

  const headers = {};
  if (process.env.ADSPOWER_KEY) headers.Authorization = `Bearer ${process.env.ADSPOWER_KEY}`;

  const response = await fetch(url, { headers });
  if (!response.ok) {
    throw new Error(`AdsPower ${path} returned HTTP ${response.status} ${response.statusText}`);
  }

  const body = await response.json();
  // The API answers 200 with code !== 0 on failure, so the status line alone
  // does not tell you whether the call worked.
  if (body.code !== 0) {
    throw new Error(`AdsPower ${path} failed: ${body.msg ?? JSON.stringify(body)}`);
  }
  return body.data;
}

/** Start a profile and return its CDP/websocket details. */
export function startProfile(userId, { openTabs = 1, headless = 0 } = {}) {
  return call('/api/v1/browser/start', {
    user_id: userId,
    open_tabs: openTabs,
    headless,
  });
}

/** Stop a profile. This actually closes the AdsPower window. */
export function stopProfile(userId) {
  return call('/api/v1/browser/stop', { user_id: userId });
}

/** Whether a profile is currently running. */
export function profileStatus(userId) {
  return call('/api/v1/browser/active', { user_id: userId });
}

/**
 * Start the profile if needed and attach Playwright over CDP.
 *
 * Returns the browser, the profile's existing context and a page. AdsPower
 * opens its own tabs, so reuse context[0] rather than creating a fresh context
 * — a new one would not carry the profile's cookies or fingerprint.
 */
export async function connectProfile(userId = process.env.ADSPOWER_USER_ID) {
  if (!userId) throw new Error('Set ADSPOWER_USER_ID or pass a profile id');

  const data = await startProfile(userId);
  const endpoint = data.ws?.puppeteer;
  if (!endpoint) {
    throw new Error(`No CDP endpoint in the start response: ${JSON.stringify(data)}`);
  }

  const browser = await chromium.connectOverCDP(endpoint);
  const context = browser.contexts()[0] ?? (await browser.newContext());
  const page = context.pages()[0] ?? (await context.newPage());

  return { browser, context, page, data };
}

/**
 * Detach without touching the profile.
 *
 * Playwright's browser.close() on a CDP connection drops the connection rather
 * than killing the browser, but say it explicitly: closing an AdsPower window
 * is stopProfile()'s job, so the fingerprint session ends the way AdsPower
 * expects.
 */
export async function disconnect(browser) {
  await browser.close();
}
