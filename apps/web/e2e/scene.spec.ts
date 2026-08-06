import { test, expect } from '@playwright/test';

test('bakery scene renders and prop is clickable', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByTestId('scene-viewport')).toBeVisible();
  const loaf = page.getByRole('button', { name: /loaf/i });
  await expect(loaf).toBeVisible();
  await loaf.click();
  await expect(page.getByTestId('companion-popover')).toBeVisible();
});
