const { expect, test } = require('@playwright/test');

const viewports = [
  { name: 'phone-320', width: 320, height: 800 },
  { name: 'phone-390', width: 390, height: 844 },
  { name: 'tablet-768', width: 768, height: 1024 },
  { name: 'tablet-1024', width: 1024, height: 768 },
];

async function expectNoHorizontalOverflow(page, route) {
  const dimensions = await page.evaluate(() => ({
    viewport: window.innerWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(
    dimensions.content,
    `${route} is ${dimensions.content - dimensions.viewport}px wider than the viewport`,
  ).toBeLessThanOrEqual(dimensions.viewport);
}

for (const viewport of viewports) {
  test.describe(viewport.name, () => {
    test.use({ viewport });

    for (const route of ['/', '/approach/', '/assessment/', '/careers/', '/login/']) {
      test(`${route} reflows without clipping`, async ({ page }, testInfo) => {
        await page.goto(route);
        await expectNoHorizontalOverflow(page, route);
        await page.screenshot({
          path: testInfo.outputPath(`${viewport.name}-${route.replaceAll('/', '') || 'home'}.png`),
          fullPage: true,
        });
      });
    }

    test('admin portal, CRM, and Django admin reflow', async ({ page }) => {
      await page.goto('/login/');
      await page.getByRole('button', { name: 'Demo Administrator' }).click();
      await expect(page).toHaveURL(/\/dashboard\/$/);

      for (const route of ['/dashboard/', '/inbox/', '/crm/', '/crm/companies/', '/crm/deals/', '/admin/']) {
        await page.goto(route);
        await expectNoHorizontalOverflow(page, route);
      }

      if (viewport.width < 651) {
        await page.goto('/crm/');
        await expect(page.locator('.contact-cards')).toBeVisible();
        await expect(page.locator('.table-wrap')).toBeHidden();
      }
    });
  });
}
