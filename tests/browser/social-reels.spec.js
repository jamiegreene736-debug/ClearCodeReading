const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');

test.use({ channel: 'chromium' });
function studio(saved = false) {
  return execFileSync(process.env.PYTHON || 'python', ['-c', `
import django, sys
django.setup()
from django.template.loader import render_to_string
from django.test import RequestFactory
from apps.social.models import SocialPost
request = RequestFactory().get('/portal/marketing/reels/new/')
post = SocialPost(is_reel=True, brief='A child wants shared reading instead of a tablet.', reel_prompt='A child turns away from a tablet, brings a book to a caregiver and settles in to read.')
if sys.argv[1] == 'saved': post.pk = 123
print(render_to_string('social/reel_form.html', {'request': request, 'post': post, 'ai_ready': True, 'busy': False, 'nav': {}}))
`, saved ? 'saved' : 'new'], { env: { ...process.env, DJANGO_SETTINGS_MODULE: 'clearcodereading.settings' }, encoding: 'utf8' });
}

for (const width of [390, 1280]) {
  test(`Reel studio fits ${width}px and exposes storyboard controls`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route('**/*', route => route.abort());
    await page.setContent(studio());
    for (const path of ['apps/core/static/css/clearcode-tailwind.css', 'apps/social/static/social/navigation.css', 'apps/social/static/social/reports.css', 'apps/social/static/social/reels.css']) {
      await page.addStyleTag({ content: fs.readFileSync(path, 'utf8') });
    }
    await expect(page.getByLabel('What should this Reel say?')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Plan a creative Reel' })).toBeEnabled();
    await expect(page.getByLabel('Storyboard · hook → action → payoff')).toContainText('tablet');
    await expect(page.getByLabel('Facebook Reel', { exact: true })).toBeChecked();
    await expect(page.getByLabel('Instagram Reel', { exact: true })).toBeChecked();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.screenshot({ path: testInfo.outputPath('reel-studio.png'), fullPage: true });
  });
}

test('unsaved edits prevent generating an older storyboard', async ({ page }) => {
  await page.route('**/*', route => route.abort());
  await page.setContent(studio(true));
  await page.addScriptTag({ path: 'apps/social/static/social/reels.js' });
  await page.getByLabel('Storyboard · hook → action → payoff').fill('A changed moving story with a tablet.');
  await page.getByRole('button', { name: 'Generate moving video' }).click();
  await expect(page.locator('[data-reel-status]')).toHaveText('Save your storyboard and captions before generating.');
});

for (const browserName of ['chromium', 'webkit']) {
  test.describe(`${browserName} generation requests`, () => {
    for (const responseType of ['json', 'html']) {
      test(`Generate uses the saved endpoint and recovers from ${responseType} errors`, async () => {
        const browser = await require('@playwright/test')[browserName].launch({ channel: browserName === 'chromium' ? 'chromium' : '' });
        try {
          const page = await browser.newPage();
          const submitted = [];
          await page.route('**/*', route => route.abort());
          await page.route('https://reels.test/**', async route => {
            const request = route.request();
            if (request.method() === 'POST') {
              submitted.push({ url: request.url(), body: request.postData() });
              return route.fulfill(responseType === 'json'
                ? { status: 400, contentType: 'application/json', body: JSON.stringify({ error: 'Test provider rejection; no video was generated.' }) }
                : { status: 502, contentType: 'text/html', body: '<h1>Server temporarily unavailable</h1>' });
            }
            return route.fulfill({ contentType: 'text/html', body: studio(true) });
          });
          await page.goto('https://reels.test/portal/marketing/reels/123/');
          await page.addScriptTag({ path: 'apps/social/static/social/reels.js' });
          // HTML's named controls mask the property; production must read the attribute.
          expect(await page.locator('[data-reel-generation]').evaluate(form => form.action.tagName)).toBe('BUTTON');
          await page.getByRole('button', { name: 'Generate moving video' }).click();
          await expect(page.locator('[data-reel-status]')).toHaveText(responseType === 'json'
            ? 'Test provider rejection; no video was generated.'
            : 'The server could not confirm the video request. Reload this draft to check its saved progress before trying again.');
          expect(submitted).toHaveLength(1);
          expect(submitted[0].url).toBe('https://reels.test/portal/marketing/reels/123/generate/');
          expect(submitted[0].body).toContain('name="action"\r\n\r\nstart');
          await expect(page.getByRole('link', { name: 'Reload saved draft' })).toBeVisible();
          await expect(page.getByRole('button', { name: 'Generate moving video' })).toBeDisabled();
        } finally {
          await browser.close();
        }
      });
    }
  });
}
