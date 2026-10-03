const { expect, test } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

// Render the real Django composer without accounts, credentials, or a database.
const render = (mode, image = false) => execFileSync(process.env.PYTHON || 'python', ['-c', `
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'clearcodereading.settings')
import django
django.setup()
from django.template.loader import render_to_string
from apps.social.models import SocialPost
post = SocialPost(pk=123, source='${mode}', facebook_caption='Saved Facebook caption', instagram_caption='Saved Instagram caption', image_data=${image ? "b'image'" : 'None'})
print(render_to_string('social/post_form.html', {'post': post, 'mode': '${mode}', 'accounts': {}, 'ai_ready': False}))
`], { encoding: 'utf8' });
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1kAAAAASUVORK5CYII=', 'base64');
const html = { manual: render('manual'), brief: render('brief'), saved: render('manual', true) };
test.use({ channel: 'chromium' });

async function openComposer(page, mode = 'manual') {
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html[mode] });
    if (url.pathname.includes('/image/')) return route.fulfill({ contentType: 'image/png', body: png });
    const assets = {
      '/static/social/preview.js': ['apps/social/static/social/preview.js', 'application/javascript'],
      '/static/social/preview.css': ['apps/social/static/social/preview.css', 'text/css'],
      '/static/css/clearcode-tailwind.css': ['apps/core/static/css/clearcode-tailwind.css', 'text/css'],
    };
    const asset = assets[url.pathname];
    if (asset) return route.fulfill({ contentType: asset[1], body: fs.readFileSync(path.resolve(asset[0])) });
    return route.abort();
  });
  await page.goto('http://preview.test/');
  await expect(page.locator('#facebook-preview-caption')).toHaveText('Saved Facebook caption');
}

test('manual captions update immediately, use fallback, and render markup as text', async ({ page }) => {
  await openComposer(page);
  const caption = 'Read together 📚\n<img src=x onerror=alert(1)> #reading';
  await page.locator('#caption').fill(caption);
  await page.locator('#instagram_caption').fill('  ');
  await expect(page.locator('#facebook-preview-caption')).toHaveText(caption);
  await expect(page.locator('#instagram-preview-caption')).toHaveText(caption);
  await expect(page.locator('#facebook-preview-caption img')).toHaveCount(0);
  await page.locator('#instagram_caption').fill('Instagram only');
  await expect(page.locator('#instagram-preview-caption')).toHaveText('Instagram only');
  await expect(page.locator('#facebook-preview-caption')).toHaveText(caption);
  await page.locator('#caption').fill('📚'.repeat(2300));
  await page.locator('#instagram_caption').fill('');
  await expect(page.locator('#instagram-preview-caption')).toHaveText('📚'.repeat(2200));
});

test('brief captions remain independent and selected networks control previews', async ({ page }) => {
  await openComposer(page, 'brief');
  await page.locator('#facebook_caption').fill('Edited generated draft');
  await page.locator('#instagram_caption').fill('');
  await expect(page.locator('#facebook-preview-caption')).toHaveText('Edited generated draft');
  await expect(page.locator('#instagram-preview-caption')).toHaveText('Your caption will appear here.');
  await page.locator('[name=post_to_facebook]').uncheck();
  await expect(page.locator('[data-preview-network=facebook]')).toBeHidden();
  await page.locator('[name=post_to_instagram]').uncheck();
  await expect(page.locator('[data-preview-empty]')).toBeVisible();
  await page.locator('[name=post_to_instagram]').check();
  await expect(page.locator('[data-preview-network=instagram]')).toBeVisible();
  await expect(page.locator('[data-preview-empty]')).toBeHidden();
});

test('links and local photos follow publishing behavior without saving', async ({ page }) => {
  await openComposer(page);
  const mutations = [];
  page.on('request', (request) => { if (request.method() !== 'GET') mutations.push(request.url()); });
  await page.locator('#link_url').fill('https://example.com/reading');
  await expect(page.locator('[data-preview-domain]')).toHaveText('example.com');
  await expect(page.locator('[data-preview-link]')).toBeVisible();
  await page.locator('#image').setInputFiles({ name: 'photo.png', mimeType: 'image/png', buffer: png });
  await expect(page.locator('[data-preview-image]').first()).toBeVisible();
  await expect(page.locator('[data-preview-image]').last()).toBeVisible();
  await expect(page.locator('[data-preview-photo-needed]')).toBeHidden();
  await expect(page.locator('[data-preview-link]')).toBeHidden();
  await expect(page.locator('[data-preview-photo-link]')).toBeVisible();
  await page.locator('#image').setInputFiles([]);
  await expect(page.locator('[data-preview-photo-needed]')).toBeVisible();
  await expect(page.locator('[data-preview-link]')).toBeVisible();
  await page.locator('#link_url').fill('javascript:alert(1)');
  await expect(page.locator('[data-preview-link]')).toBeHidden();
  expect(mutations).toEqual([]);
});

test('saved photo returns when replacement is cleared and invalid photos explain the error', async ({ page }) => {
  await openComposer(page, 'saved');
  const image = page.locator('[data-preview-image]').first();
  const saved = await image.getAttribute('src');
  await expect(image).toBeVisible();
  await page.locator('#image').setInputFiles({ name: 'new.png', mimeType: 'image/png', buffer: png });
  await expect(image).toHaveAttribute('src', /^blob:/);
  await page.locator('#image').setInputFiles([]);
  await expect(image).toHaveAttribute('src', saved);
  await page.locator('#image').setInputFiles({ name: 'bad.png', mimeType: 'image/png', buffer: Buffer.from('invalid') });
  await expect(page.locator('[data-preview-image-error]')).toContainText('could not be previewed');
  await expect(image).toBeHidden();
  await page.locator('#image').setInputFiles([]);
  await expect(image).toBeVisible();
  await expect(page.locator('[data-preview-image-error]')).toBeHidden();
});

for (const width of [320, 390, 1280]) {
  test(`long captions expand without submitting or overflowing at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await openComposer(page, 'brief');
    await page.locator('#facebook_caption').fill('Reading together is a small step.\n'.repeat(25));
    const card = page.locator('[data-preview-network=facebook]');
    await card.getByRole('button', { name: 'See more' }).click();
    await expect(card.getByRole('button', { name: 'See less' })).toHaveAttribute('aria-expanded', 'true');
    await card.getByRole('button', { name: 'See less' }).click();
    await page.locator('#instagram_caption').fill('a'.repeat(2200));
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await expect(page).toHaveURL('http://preview.test/');
  });
}

test('photo preview preserves the image and remains readable on desktop and mobile', async ({ page }, testInfo) => {
  await openComposer(page);
  await page.locator('#caption').fill('Small moments. Growing confidence.\n\nMake room for a little reading together today. 📚\n#ClearCodeReading');
  await page.locator('#instagram_caption').fill('');
  await page.locator('#image').setInputFiles('marketing-website/assets/images/family-reading-practice-640.webp');
  const image = page.locator('[data-preview-image]').first();
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((element) => element.naturalWidth)).toBeGreaterThan(0);
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({ path: testInfo.outputPath(`preview-${width}.png`), fullPage: true });
  }
});
