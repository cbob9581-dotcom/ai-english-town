import { test, expect } from '@playwright/test';

test('plaza skeleton renders and default npc is clickable', async ({ page }) => {
  await page.goto('/');
  await page.getByText('开始语音').click();              // 建立 WS → 服务端推 plaza skeleton
  await expect(page.getByTestId('scene-viewport')).toBeVisible();
  const npc = page.getByRole('button', { name: /Tom/i }); // plaza 默认 NPC（greeter）
  await expect(npc).toBeVisible();
  await npc.click();
});
