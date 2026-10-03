const { expect, test } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');

const pages = JSON.parse(execFileSync(process.env.PYTHON || 'python', ['-c', `
import os, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'clearcodereading.settings')
import django
django.setup()
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import resolve, reverse
from datetime import date
from apps.social.models import ContentPlan
pages = {}
for route, template in [('queue','queue'), ('calendar','calendar'), ('planner','planner')]:
    request = RequestFactory().get(reverse('social:' + route))
    request.resolver_match = resolve(request.path)
    context = {'request': request, 'tab': 'scheduled', 'accounts': {}, 'posts': [], 'tabs': [('scheduled','Scheduled'), ('posted','Posted'), ('drafts','Drafts'), ('attention','Needs attention')], 'counts': {}, 'plan': ContentPlan(), 'cards': [], 'focus': date(2026,10,1), 'today_month': '2026-10', 'previous': '2026-09', 'following': '2026-11'}
    pages[request.path] = render_to_string('social/' + template + '.html', context)
print(json.dumps(pages))
`], { encoding: 'utf8' }));
test.use({ channel: 'chromium' });

for (const width of [320, 390, 1280]) {
  test(`workspace navigation explains destinations and works at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.route('**/*', (route) => {
      const url = new URL(route.request().url());
      if (pages[url.pathname]) return route.fulfill({ contentType: 'text/html', body: pages[url.pathname] });
      const assets = {
        '/static/social/navigation.css': 'apps/social/static/social/navigation.css',
        '/static/css/clearcode-tailwind.css': 'apps/core/static/css/clearcode-tailwind.css',
      };
      if (assets[url.pathname]) return route.fulfill({ contentType: 'text/css', body: fs.readFileSync(assets[url.pathname]) });
      return route.abort();
    });
    await page.goto('http://preview.test/portal/marketing/');
    const nav = page.getByRole('navigation', { name: 'Social marketing', exact: true });
    await expect(nav.locator('[aria-current=page]')).toContainText('Posts');
    for (const [label, heading] of [['Social post calendar', 'Social post calendar'], ['AI content plan', 'AI content plan'], ['Posts', 'Scheduled']]) {
      await nav.getByRole('link', { name: new RegExp('^' + label) }).click();
      await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
      await expect(nav.locator('[aria-current=page]')).toContainText(label);
      await expect(nav.getByRole('link')).toHaveCount(4);
      await expect(page.getByRole('link', { name: 'Calendar view', exact: true })).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await page.screenshot({ path: testInfo.outputPath(`${label.replaceAll(' ', '-')}-${width}.png`), fullPage: true });
    }
    await nav.getByRole('link').first().focus();
    await page.keyboard.press('Tab');
    await expect(nav.getByRole('link', { name: /^Social post calendar/ })).toBeFocused();
  });
}
