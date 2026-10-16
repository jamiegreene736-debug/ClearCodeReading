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
