import { expect, test } from '@playwright/test';

test('renders inline content headlessly', async ({ page }) => {
  await page.setContent('<h1 id="title">hello playwright</h1>');
  await expect(page.locator('#title')).toHaveText('hello playwright');
});

test('renders and screenshots without a display server', async ({ page }, testInfo) => {
  expect(testInfo.project.use.headless).toBe(true);
  expect(process.env.DISPLAY ?? '').toBe('');

  await page.setContent('<div style="width:100px;height:100px;background:#c15f3c"></div>');
  const shot = await page.locator('div').screenshot();
  expect(shot.byteLength).toBeGreaterThan(0);
});

test('captures console errors from the page', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text());
  });

  await page.setContent('<script>console.error("boom")</script>');
  await expect.poll(() => errors).toContain('boom');
});
