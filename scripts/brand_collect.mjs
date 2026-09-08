/**
 * 节点 02 · 品牌官网采集。
 *
 * 从一个商品列表页把商品图抓下来，连同商品链接和名称写成 sources.csv，
 * 交给 collect.py --ingest 收进台账。分工是：这边负责跟浏览器打交道，
 * 出处登记和去重仍然只有台账那一处，不在两个地方各写一份。
 *
 * 抓下来的图**只能内部对标**。台账会把它们标成 use: internal，节点 07
 * 出交付页时不会用；交付页上的图只能来自节点 05 自己生成的。
 *
 *   node scripts/brand_collect.mjs <列表页URL> --probe
 *   node scripts/brand_collect.mjs <列表页URL> --out 导出/seed --limit 36
 *   python -X utf8 scripts/collect.py --plan plan.json --ingest 导出/seed \
 *       --channel brand --direction 1 --source "Seed 官网 Knitwear"
 */
import { mkdir, writeFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';
import { chromium } from '@playwright/test';
import { proxyFromEnv } from './lib/env.mjs';

const VERSION = '2026-09-08a';
// 主动中止用的信号 —— ESM 顶层不能 return，又必须走到 finally 把浏览器关掉
const STOP = '__stop__';

// 常见电商站的商品卡片写法。--probe 会把每条命中多少个报出来，
// 让操作的人挑，而不是脚本猜一个然后静悄悄抓错东西。
const CARD_GUESSES = [
  'a[href*="/products/"]',            // Shopify
  'a[href*="/product/"]',
  '[class*="product-card"] a',
  '[class*="product-item"] a',
  '[data-product-id] a',
  'li[class*="product"] a',
];

const { values: opts, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    out: { type: 'string', default: 'brand-export' },
    limit: { type: 'string', default: '36' },
    pages: { type: 'string', default: '1' },
    card: { type: 'string' },
    delay: { type: 'string', default: '1500' },
    'min-bytes': { type: 'string', default: '20000' },
    probe: { type: 'boolean', default: false },
    timeout: { type: 'string', default: '30000' },
  },
});

const listing = positionals[0];
if (!listing) {
  console.error('用法：node scripts/brand_collect.mjs <列表页URL> [--probe] [--out DIR] [--limit N]');
  process.exit(2);
}
console.error(`brand_collect 版本 ${VERSION}`);

const limit = Number(opts.limit);
const pages = Number(opts.pages);
const delay = Number(opts.delay);

/** robots.txt 说不许就不抓。没有绕过开关 —— 需要抓就先去拿书面许可。 */
async function robotsAllows(request, target) {
  const url = new URL(target);
  let body;
  try {
    const response = await request.get(`${url.origin}/robots.txt`, { timeout: 15000 });
    if (!response.ok()) return { allowed: true, why: `robots.txt 返回 ${response.status()}，按允许处理` };
    body = await response.text();
  } catch {
    return { allowed: true, why: 'robots.txt 取不到，按允许处理' };
  }

  // 只看 User-agent: * 那一段，取最长匹配的规则 —— 和爬虫的通行做法一致
  let applies = false;
  let best = null;
  for (const raw of body.split('\n')) {
    const line = raw.split('#')[0].trim();
    if (!line) continue;
    const [rawKey, ...rest] = line.split(':');
    const key = rawKey.trim().toLowerCase();
    const value = rest.join(':').trim();
    if (key === 'user-agent') applies = value === '*';
    else if (applies && (key === 'allow' || key === 'disallow') && value) {
      if (url.pathname.startsWith(value) && (!best || value.length > best.path.length)) {
        best = { rule: key, path: value };
      }
    }
  }
  if (best?.rule === 'disallow') {
    return { allowed: false, why: `robots.txt 里 Disallow: ${best.path} 覆盖了这个路径` };
  }
  return { allowed: true, why: best ? `robots.txt 里 Allow: ${best.path}` : 'robots.txt 没有限制这个路径' };
}

const browser = await chromium.launch({ proxy: proxyFromEnv(listing) });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();

try {
  const verdict = await robotsAllows(context.request, listing);
  console.error(`robots：${verdict.why}`);
  if (!verdict.allowed) {
    console.error('不抓。这不是可以用开关绕过去的东西 —— 需要的话先向品牌方拿书面许可。');
    process.exitCode = 1;
  } else {

  await page.goto(listing, { waitUntil: 'domcontentloaded', timeout: Number(opts.timeout) });
  await page.waitForTimeout(1200);

  if (opts.probe) {
    console.log(`\n列表页：${await page.title()}\n`);
    console.log('候选选择器　命中数　示例链接');
    for (const selector of CARD_GUESSES) {
      const n = await page.locator(selector).count();
      const sample = n ? await page.locator(selector).first().getAttribute('href') : '';
      console.log(`  ${selector.padEnd(30)}${String(n).padStart(4)}   ${sample || ''}`);
    }
    console.log('\n挑命中数看起来对的那条，用 --card "<选择器>" 正式跑。');
    console.log('全是 0 说明商品是脚本渲染出来的，把 --timeout 调大，或者换个列表页。');
  } else {
    const selector = opts.card || CARD_GUESSES.find(async (s) => (await page.locator(s).count()) > 0);
    let card = opts.card;
    if (!card) {
      for (const guess of CARD_GUESSES) {
        if (await page.locator(guess).count() > 0) { card = guess; break; }
      }
    }
    if (!card) {
      console.error('一个商品卡片都没找到。先跑 --probe 看看这个站是什么结构。');
      process.exitCode = 1;
      throw new Error(STOP);
    }
    console.error(`用选择器：${card}`);

    const seen = new Set();
    const rows = [];
    // 丢掉的要记账。静悄悄跳过是最难查的那种 bug —— 跑完只看到"收了 0 张"，
    // 不知道是没找到、下载失败，还是全被当成缩略图滤掉了。
    const dropped = { noImage: 0, dupe: 0, fetch: 0, tooSmall: 0 };
    const minBytes = Number(opts['min-bytes']);
    await mkdir(opts.out, { recursive: true });

    for (let pageNo = 1; pageNo <= pages && rows.length < limit; pageNo += 1) {
      if (pageNo > 1) {
        const next = new URL(listing);
        next.searchParams.set('page', String(pageNo));
        await page.goto(next.toString(), { waitUntil: 'domcontentloaded', timeout: Number(opts.timeout) });
        await page.waitForTimeout(delay);
      }
      // 懒加载的图不滚到就没有 src
      await page.mouse.wheel(0, 4000);
      await page.waitForTimeout(900);

      const found = await page.locator(card).evaluateAll((nodes) => nodes.map((node) => {
        const img = node.querySelector('img') || node.parentElement?.querySelector('img');
        const src = img?.currentSrc || img?.src
          || img?.getAttribute('data-src') || img?.getAttribute('data-srcset')?.split(' ')[0] || '';
        return {
          href: node.href || '',
          src,
          name: (img?.alt || node.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80),
        };
      }));

      for (const item of found) {
        if (rows.length >= limit) break;
        if (!item.src || !item.href) { dropped.noImage += 1; continue; }
        if (seen.has(item.href)) { dropped.dupe += 1; continue; }
        seen.add(item.href);

        const absolute = new URL(item.src, page.url()).toString();
        let buffer;
        try {
          const response = await context.request.get(absolute, { timeout: 30000 });
          if (!response.ok()) { dropped.fetch += 1; continue; }
          buffer = await response.body();
        } catch { dropped.fetch += 1; continue; }
        if (buffer.length < minBytes) { dropped.tooSmall += 1; continue; }

        const extension = (absolute.split('?')[0].match(/\.(jpe?g|png|webp)$/i)?.[1] || 'jpg').toLowerCase();
        const filename = `${String(rows.length + 1).padStart(3, '0')}.${extension === 'jpeg' ? 'jpg' : extension}`;
        await writeFile(`${opts.out}/${filename}`, buffer);
        rows.push({ filename, source_url: item.href, author: new URL(item.href).hostname, note: item.name });
        process.stderr.write(`\r收 ${rows.length}/${limit}`);
        await page.waitForTimeout(delay);      // 一张一停，别把人家站点打疼
      }
    }
    process.stderr.write('\n');

    const escape = (value) => `"${String(value).replace(/"/g, '""')}"`;
    await writeFile(`${opts.out}/sources.csv`,
      'filename,source_url,author,note\n'
      + rows.map((r) => [r.filename, r.source_url, r.author, r.note].map(escape).join(',')).join('\n') + '\n',
      'utf8');

    const skipped = Object.entries(dropped).filter(([, n]) => n > 0);
    console.log(`\n收了 ${rows.length} 张 → ${opts.out}/（含 sources.csv，逐张有出处）`);
    if (skipped.length) {
      const label = { noImage: '卡片里没图', dupe: '同一商品重复', fetch: '下载失败', tooSmall: `小于 ${minBytes} 字节（当缩略图丢了）` };
      console.log('跳过：' + skipped.map(([k, n]) => `${label[k]} ${n}`).join('　'));
      if (dropped.tooSmall && !rows.length) {
        console.log('全被当成缩略图了 —— 这个站的图可能就是小尺寸，把 --min-bytes 调低再跑。');
      }
    }
    console.log('下一步：');
    console.log(`  python -X utf8 scripts/collect.py --plan plan.json --ingest ${opts.out} \\`);
    console.log('      --channel brand --direction 1 --source "品牌官网"');
  }
  }
} catch (error) {
  if (error.message !== STOP) throw error;
} finally {
  await context.close();
  await browser.close();
}
