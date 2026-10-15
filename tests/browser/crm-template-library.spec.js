const { expect, test } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const html = execFileSync(process.env.PYTHON || 'python', ['-c', `
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'clearcodereading.settings')
import django
django.setup()
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse, resolve
from apps.users.models import CustomUser
from apps.crm_email.models import EmailTemplate
from apps.crm_email.referral_templates import REFERRAL_TEMPLATES
from types import SimpleNamespace
from unittest.mock import patch
patch("apps.crm.templatetags.crm_display.pending_routing_people_count", return_value=0).start()
patch("apps.crm.templatetags.crm_display.pending_completion_people_count", return_value=0).start()
patch("apps.crm.templatetags.crm_display.hiring_queue_counts", return_value={"pending_intake": 0}).start()
request = RequestFactory().get(reverse('crm_email_templates'))
request.resolver_match = resolve(request.path)
request.user = CustomUser(email='editor@example.com', is_superuser=True)
print(render_to_string('crm/email_templates.html', {'request': request, 'active_tab': 'templates', 'templates': [EmailTemplate(pk=7, name='Welcome message', subject='Welcome to ClearCode')], 'pipeline_templates': [{'spec': SimpleNamespace(name='Family enrollment', key='stage_family_enrollment'), 'subject': 'A first step', 'body': 'Hello {{contact.firstname}}, here is your next step.'}], 'referral_templates': REFERRAL_TEMPLATES.values()}))
`], { encoding: 'utf8' });
test.use({ channel: 'chromium' });
for (const width of [390, 1280]) {
  test(`CRM template library is clear and usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route('**/*', route => route.request().url() === 'http://templates.test/' ? route.fulfill({ contentType: 'text/html', body: html }) : route.abort());
    await page.goto('http://templates.test/');
    await expect(page.getByRole('heading', { name: 'Email templates', exact: true })).toBeVisible();
    const nav = page.getByRole('navigation', { name: 'Email and notification settings' });
    await expect(nav.getByRole('link', { name: 'Email templates' })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByRole('link', { name: 'Edit Welcome message' })).toHaveAttribute('href', /templates\/7\//);
    await page.locator('summary').filter({ hasText: 'Family enrollment' }).click();
    await expect(page.getByText('Hello {{contact.firstname}}, here is your next step.')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Edit shared wording' })).toHaveAttribute('href', /automated\/stage_family_enrollment\//);
    await page.locator('summary').filter({ hasText: 'Referral Partner - General' }).click();
    await expect(page.getByRole('link', { name: 'Customize a copy of Referral Partner - General', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}
