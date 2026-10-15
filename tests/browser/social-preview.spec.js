const { expect, test } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

// Render the real Django composer without accounts, credentials, or a database.
const render = (mode, image = false, carousel = false, auto = false) => execFileSync(process.env.PYTHON || 'python', ['-c', `
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'clearcodereading.settings')
import django
django.setup()
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import resolve
request = RequestFactory().get("/portal/marketing/posts/123/")
request.resolver_match = resolve(request.path)
from apps.social.models import SocialPost
post = SocialPost(pk=123, source='${mode}', facebook_caption='Saved Facebook caption', instagram_caption='Saved Instagram caption', image_data=${image ? "b'image'" : 'None'})
from types import SimpleNamespace
slides = [SimpleNamespace(pk=456, image_name='second.jpg')] if ${carousel ? 'True' : 'False'} else []
if slides: post.carousel_plan = {'slides': ['A warm reading corner', 'A sunny reading lamp'], 'completed': 0}
print(render_to_string('social/post_form.html', {'request': request, 'post': post, 'mode': '${mode}', 'accounts': {}, 'ai_ready': False, 'csrf_token': 'test-csrf', 'slides': slides, 'carousel_pending': bool(slides), 'auto_generate': ${auto ? 'True' : 'False'}}))
`], { encoding: 'utf8' });
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1kAAAAASUVORK5CYII=', 'base64');
const html = { manual: render('manual'), brief: render('brief'), saved: render('manual', true), carousel: render('manual', true, true), auto: render('brief', true, true, true) };
test.use({ channel: 'chromium' });

async function openComposer(page, mode = 'manual') {
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html[mode] });
    if (url.pathname.includes('/image/') || url.pathname.includes('/slides/')) return route.fulfill({ contentType: 'image/png', body: png });
    const assets = {
      '/static/social/preview.js': ['apps/social/static/social/preview.js', 'application/javascript'],
      '/static/social/reports.css': ['apps/social/static/social/reports.css', 'text/css'],
      '/static/social/navigation.css': ['apps/social/static/social/navigation.css', 'text/css'],
      '/static/social/preview.css': ['apps/social/static/social/preview.css', 'text/css'],
      '/static/css/clearcode-tailwind.css': ['apps/core/static/css/clearcode-tailwind.css', 'text/css'],
    };
    const asset = assets[url.pathname];
    if (asset) return route.fulfill({ contentType: asset[1], body: fs.readFileSync(path.resolve(asset[0])) });
    if (url.pathname.endsWith('/generate-slide/')) return route.fallback();
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


test('carousel uploads reorder and remove photos in the saved payload and both previews', async ({ page }) => {
  await openComposer(page, 'saved');
  await page.locator('#images').setInputFiles([
    { name: 'second.png', mimeType: 'image/png', buffer: png },
    { name: 'third.png', mimeType: 'image/png', buffer: png },
  ]);
  await expect(page.locator('[data-photo-list] li')).toHaveCount(3);
  await expect(page.locator('[data-slide-count]').first()).toHaveText('1 / 3');
  const cover = await page.locator('[data-preview-image]').first().getAttribute('src');
  await page.locator('[data-slide-next]').first().click();
  await expect(page.locator('[data-slide-count]').last()).toHaveText('2 / 3');
  expect(await page.locator('[data-preview-image]').last().getAttribute('src')).not.toBe(cover);
  await page.getByRole('button', { name: 'Earlier photo 3', exact: true }).click();
  await expect(page.locator('[data-photo-list] li').nth(1)).toContainText('third.png');
  expect(await page.locator('#images').evaluate((node) => [...node.files].map((file) => file.name))).toEqual(['third.png', 'second.png']);
  await expect(page.locator('[name=photo_order]')).toHaveValue('[' + '"cover","upload:0","upload:1"' + ']');
  await page.getByRole('button', { name: 'Remove photo 1', exact: true }).click();
  await expect(page.locator('[data-photo-list] li')).toHaveCount(2);
  await page.getByRole('button', { name: 'Remove photo 1', exact: true }).click();
  await page.getByRole('button', { name: 'Remove photo 1', exact: true }).click();
  await expect(page.locator('[value=schedule]')).toBeDisabled();
  await expect(page.locator('[data-preview-photo-needed]')).toBeVisible();
});

test('carousel enforces ten-photo limit and keeps controls within a phone viewport', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await openComposer(page);
  await page.locator('#images').setInputFiles(Array.from({ length: 11 }, (_, i) => ({ name: `photo-${i}.png`, mimeType: 'image/png', buffer: png })));
  await expect(page.locator('p[data-photo-error]')).toHaveText('Choose up to 10 photos.');
  await expect(page.locator('[value=post_now]')).toBeDisabled();
  await page.getByRole('button', { name: 'Remove photo 11', exact: true }).click();
  await expect(page.locator('p[data-photo-error]')).toBeHidden();
  await expect(page.locator('[value=post_now]')).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('AI carousel generates one slide per request, explains failure, and resumes', async ({ page }) => {
  await openComposer(page, 'carousel');
  let calls = 0;
  await page.route('**/generate-slide/', async (route) => {
    calls += 1;
    expect(route.request().method()).toBe('POST');
    if (calls === 2) return route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ error: 'AI generation is temporarily limited.' }) });
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ completed: calls === 1 ? 1 : 2, total: 2 }) });
  });
  await expect(page.locator('[value=post_now]')).toBeDisabled();
  await page.locator('[data-generate-slides]').click();
  await expect(page.locator('[data-generation-progress]')).toContainText('Completed slides are saved');
  expect(calls).toBe(2);
  await expect(page.locator('[data-slide-progress]')).toHaveAttribute('value', '1');
  await expect(page.locator('[data-slide-progress]')).toHaveAttribute('max', '2');
  await expect(page.locator('[data-generate-slides]')).toBeEnabled();
  await page.locator('[data-generate-slides]').click();
  await expect.poll(() => calls).toBe(3);
});

for (const mode of ['manual', 'brief']) {
  test(`carousel shortcuts are clear and reach their controls in ${mode} mode`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openComposer(page, mode);
    const guide = page.getByRole('region', { name: 'Create a single image or a carousel' });
    await expect(guide).toBeVisible();
    await expect(guide).toContainText('Instagram carousel and a Facebook multi-photo post');
    const formTop = await page.locator('#social-post-form').evaluate(element => element.getBoundingClientRect().top);
    const guideBottom = await guide.evaluate(element => element.getBoundingClientRect().bottom);
    expect(guideBottom).toBeLessThan(formTop);
    const mutations = [];
    page.on('request', request => { if (request.method() !== 'GET') mutations.push(request.url()); });
    await guide.getByRole('link', { name: 'Upload carousel images' }).click();
    await expect(page.locator('#images')).toBeInViewport();
    await guide.getByRole('link', { name: 'Create an AI carousel' }).click();
    await expect(page.locator('#ai-carousel')).toBeFocused();
    await expect(page.locator('[name=slide_count]')).toBeInViewport();
    await guide.getByRole('link', { name: 'Single image' }).click();
    await expect(page.locator('#image')).toBeInViewport();
    expect(mutations).toEqual([]);
    for (const width of [320, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    }
  });
}

for (const width of [390, 1280]) {
  test(`generation progress stays in view at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await openComposer(page, 'brief');
    await page.locator('[name="generate_image"]').check();
    await page.locator('#social-post-form').evaluate(form => {
      form.addEventListener('submit', event => event.preventDefault());
    });
    await page.locator('[value="draft_ai"]').click();
    await expect(page.locator('#generation-message')).toHaveText('Creating your captions and photo…');
    const status = page.locator('#generation-status');
    await expect(status).toBeInViewport();
    const bounds = await status.boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
    await page.screenshot({ path: `/tmp/clearcode-generation-${width}.png` });
  });
}

test('AI carousel option and uploads choose the intended image source', async ({ page }) => {
  await openComposer(page, 'brief');
  const carousel = page.locator('[name=generate_carousel]');
  const single = page.locator('[name=generate_image]');
  await single.check();
  await carousel.check();
  await expect(single).not.toBeChecked();
  await page.locator('#images').setInputFiles([
    { name: 'one.png', mimeType: 'image/png', buffer: png },
    { name: 'two.png', mimeType: 'image/png', buffer: png },
  ]);
  await expect(carousel).not.toBeChecked();
  await expect(single).not.toBeChecked();
  await expect(page.locator('[data-photo-summary]')).toContainText('2-image carousel ready');
  await expect(page.locator('[name=photo_order]')).toHaveValue('["upload:0","upload:1"]');
});

test('integrated draft starts slide generation automatically and can resume a failure', async ({ page }) => {
  let calls = 0;
  await page.route('**/generate-slide/', route => {
    calls += 1;
    return route.fulfill({ status: 429, contentType: 'application/json', body: JSON.stringify({ error: 'Try again later.' }) });
  });
  await openComposer(page, 'auto');
  await expect(page.locator('[data-generation-progress]')).toContainText('Try again later.');
  expect(calls).toBe(1);
  await expect(page.locator('[data-generate-slides]')).toBeEnabled();
  await page.locator('[data-generate-slides]').click();
  await expect.poll(() => calls).toBe(2);
});
