import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.goto('/');
});

test('rejects a malformed email', async ({ page }) => {
  await page.getByLabel('Email').fill('not-an-email');
  await page.getByLabel('Password').fill('correct-horse');
  await page.getByRole('button', { name: 'Sign in' }).click();

  await expect(page.getByRole('alert')).toHaveText('Enter a valid email address.');
});

test('rejects a short password', async ({ page }) => {
  await page.getByLabel('Email').fill('ada@example.com');
  await page.getByLabel('Password').fill('short');
  await page.getByRole('button', { name: 'Sign in' }).click();

  await expect(page.getByRole('alert')).toHaveText('Password must be at least 8 characters.');
});

test('signs in with valid credentials', async ({ page }) => {
  await page.getByLabel('Email').fill('ada@example.com');
  await page.getByLabel('Password').fill('correct-horse');
  await page.getByRole('button', { name: 'Sign in' }).click();

  await expect(page.getByText('Welcome back!')).toBeVisible();
  await expect(page.getByRole('alert')).toBeHidden();
});
