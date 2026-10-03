const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');

function composer(mode, ready = false) {
  return execFileSync(process.env.TEST_PYTHON || 'python', ['-c', `
import django, sys
django.setup()
from django.template.loader import render_to_string
from apps.social.models import SocialPost
post = SocialPost()
if sys.argv[2] == 'ready':
    post.facebook_caption = 'Reviewed Facebook caption'
    post.instagram_caption = 'Reviewed Instagram caption'
    post.image_data = b'image'
print(render_to_string('social/post_form.html', {'post': post, 'mode': sys.argv[1], 'accounts': {}}))
`, mode, ready ? 'ready' : 'empty'], {
    env: { ...process.env, DJANGO_SETTINGS_MODULE: 'clearcodereading.settings' },
    encoding: 'utf8',
  });
}

for (const mode of ['manual', 'brief']) {
  test(`${mode} composer requires content only for selected platforms`, async ({ page }) => {
    await page.route('**/*', route => route.abort());
    await page.setContent(composer(mode, mode === 'brief'));
    const schedule = page.getByRole('button', { name: 'Schedule', exact: true });
    const publish = page.getByRole('button', { name: 'Post now', exact: true });
    const facebook = page.locator('[name="post_to_facebook"]');
    const instagram = page.locator('[name="post_to_instagram"]');
    const caption = page.locator(mode === 'manual' ? '[name="caption"]' : '[name="facebook_caption"]');
    await instagram.uncheck();
    await caption.fill('   ');
    await expect(schedule).toBeDisabled();
    await expect(publish).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Save draft', exact: true })).toBeEnabled();
    await caption.fill('Ready for Facebook');
    await expect(schedule).toBeEnabled();
    await expect(publish).toBeEnabled();
    await instagram.check();
    if (mode === 'manual') {
      await expect(schedule).toBeDisabled();
      await page.locator('[name="image"]').setInputFiles({ name: 'photo.png', mimeType: 'image/png', buffer: Buffer.from('image') });
      await expect(schedule).toBeEnabled();
      await page.locator('[name="image"]').setInputFiles([]);
      await expect(schedule).toBeDisabled();
    } else {
      await expect(schedule).toBeEnabled();
      await page.locator('[name="instagram_caption"]').fill(' ');
      await expect(schedule).toBeDisabled();
    }
    await instagram.uncheck();
    await facebook.uncheck();
    await expect(schedule).toBeDisabled();
    await expect(publish).toBeDisabled();
  });
}

test('new brief cannot schedule or post before captions exist', async ({ page }) => {
  await page.route('**/*', route => route.abort());
  await page.setContent(composer('brief'));
  await expect(page.getByRole('button', { name: 'Schedule', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Post now', exact: true })).toBeDisabled();
  await expect(page.locator('#publish-requirements')).toContainText('Add a Facebook caption.');
  await expect(page.locator('#publish-requirements')).toContainText('Add a photo for Instagram.');
});

test('month and day navigation preserve the edited time', async ({ page }) => {
  const html = execFileSync(process.env.TEST_PYTHON || 'python', ['-c', `
import django
django.setup()
from datetime import date
from django.template.loader import render_to_string
from apps.social.models import SocialPost
from apps.social.views import _month
print(render_to_string('social/schedule.html', {
    'post': SocialPost(pk=1), 'focus': date(2026, 12, 1),
    'selected_date': '2026-12-31', 'selected_time': '09:00',
    'previous': '2026-11', 'following': '2027-01',
    'weeks': _month(date(2026, 12, 1), None, date(2026, 12, 31)),
}))
`], { env: { ...process.env, DJANGO_SETTINGS_MODULE: 'clearcodereading.settings' }, encoding: 'utf8' });
  await page.route('**/*', route => route.request().isNavigationRequest()
    ? route.fulfill({ contentType: 'text/html', body: html }) : route.abort());
  await page.goto('http://calendar.test/schedule/');
  await page.locator('#time').fill('14:35');
  await page.getByRole('link', { name: 'Next month' }).click();
  await expect(page).toHaveURL(/month=2027-01&date=2026-12-31&time=14%3A35/);
  await page.locator('#time').fill('16:45');
  await page.getByRole('link', { name: '15', exact: true }).click();
  await expect(page).toHaveURL(/date=2026-12-15&time=16%3A45/);
});
