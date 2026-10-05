const { expect, test } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');

const pages = JSON.parse(execFileSync(process.env.PYTHON || 'python', ['-c', `
import os, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'clearcodereading.settings')
import django
django.setup()
from django.template import Template, Context
from django.template.loader import render_to_string
from django.test import override_settings
with override_settings(TURNSTILE_SITE_KEY='test-site-key'):
    pages = {name: render_to_string(name, {'validlink': True, 'slots': [1]}) for name in ['_early_interest_survey.html', 'assessment.html', 'contact.html', 'support.html', 'careers.html', 'resources.html', '_newsletter_signup.html', 'registration/login.html', 'registration/accept_invitation.html', 'resources/signin.html', 'resources/setup.html', 'crm/newsletter_unsubscribe.html', 'crm/inventory_intake.html', 'crm/inventory_public.html', 'crm/inventory_booking.html', 'crm/consultation_booking.html']}
    pages['forms'] = Template('''{% load bot_protection %}
      <form id="first">{% bot_fields "signup" %}<input name="email"><button>Send</button><button type="submit" disabled id="unavailable">Unavailable</button></form>
      <form id="second">{% bot_fields "newsletter" %}<input type="submit" value="Subscribe"></form>
    ''').render(Context({}))
with override_settings(TURNSTILE_SITE_KEY='', DEBUG=False):
    pages['unconfigured'] = Template('{% load bot_protection %}<form>{% bot_fields "signup" %}<button>Send</button></form>').render(Context({}))
print(json.dumps(pages))
`], { encoding: 'utf8' }));
test.use({ channel: 'chromium' });

async function openPage(page, name, available = true) {
  await page.addInitScript(({ available }) => {
    window.challenges = [];
    if (available) window.turnstile = {
      render(el, options) {
        window.challenges.push({ el, options });
        return String(window.challenges.length);
      },
    };
    window.submissions = 0;
    document.addEventListener('submit', event => {
      event.preventDefault();
      window.submissions++;
    });
  }, { available });
  await page.route('**/*', route => {
    if (route.request().url() === 'http://preview.test/') {
      return route.fulfill({ contentType: 'text/html', body: pages[name] });
    }
    if (route.request().url().includes('/static/css/clearcode-tailwind.css')) {
      return route.fulfill({ contentType: 'text/css', body: fs.readFileSync('apps/core/static/css/clearcode-tailwind.css') });
    }
    return route.abort();
  });
  await page.goto('http://preview.test/');
}

async function callback(page, index, name = 'callback', token = 'test-token') {
  await page.evaluate(({ index, name, token }) => window.challenges[index].options[name](token), { index, name, token });
}

async function expectVerificationMessage(page, action) {
  const messages = [];
  const handleDialog = async dialog => {
    messages.push(dialog.message());
    await dialog.accept();
  };
  page.on('dialog', handleDialog);
  try {
    await action();
    expect(messages).toEqual(["Please verify you're human before continuing."]);
  } finally {
    page.off('dialog', handleDialog);
  }
}

test('survey cannot advance by click or Enter before verification, and prompts again on expiry', async ({ page }) => {
  await openPage(page, '_early_interest_survey.html');
  const next = page.locator('[data-survey-next]');
  await page.locator('[name="name"]').fill('Test Visitor');
  await expect(next).toBeEnabled();
  await expectVerificationMessage(page, () => next.click());
  await expect(page.locator('[data-turnstile-sitekey]')).toBeFocused();
  await expectVerificationMessage(page, () => page.locator('[name="name"]').press('Enter'));
  await expect(page.locator('[data-survey-progress-label]')).toHaveText('Question 1 of 10');
  await callback(page, 0);
  await expect(next).toBeEnabled();
  await next.click();
  await expect(page.locator('[data-survey-progress-label]')).toHaveText('Question 2 of 10');
  await callback(page, 0, 'expired-callback', '');
  await expect(next).toBeEnabled();
  await expect(page.locator('[data-survey-submit]')).toBeEnabled();
  await expect(page.locator('[data-survey-back]')).toBeEnabled();
  await expectVerificationMessage(page, () => next.click());
  await expect(page.locator('[data-survey-progress-label]')).toHaveText('Question 2 of 10');
  await callback(page, 0);
  await expect(next).toBeEnabled();
});

test('each form requires its own verification and blocks programmatic submit events', async ({ page }) => {
  await openPage(page, 'forms');
  const send = page.getByRole('button', { name: 'Send', exact: true });
  await expect(send).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Subscribe' })).toBeEnabled();
  await expectVerificationMessage(page, () => page.locator('#first').evaluate(form => form.requestSubmit()));
  expect(await page.evaluate(() => window.submissions)).toBe(0);
  await callback(page, 0);
  await expect(send).toBeEnabled();
  await expect(page.locator('#unavailable')).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Subscribe' })).toBeEnabled();
  await expectVerificationMessage(page, () => page.getByRole('button', { name: 'Subscribe' }).click());
  expect(await page.evaluate(() => window.submissions)).toBe(0);
  await send.click();
  expect(await page.evaluate(() => window.submissions)).toBe(1);
  for (const failure of ['error-callback', 'timeout-callback', 'expired-callback']) {
    await callback(page, 0, failure, '');
    await expect(send).toBeEnabled();
    await expectVerificationMessage(page, () => send.click());
    expect(await page.evaluate(() => window.submissions)).toBe(1);
    await callback(page, 0);
    await expect(send).toBeEnabled();
  }
});

for (const name of ['forms', 'unconfigured']) {
  test(`${name}: unavailable verification explains blocked attempts`, async ({ page }) => {
    await openPage(page, name, false);
    await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled();
    await expectVerificationMessage(page, () => page.getByRole('button', { name: 'Send', exact: true }).click());
    await expectVerificationMessage(page, () => page.locator('form').first().evaluate(form => form.requestSubmit()));
    expect(await page.evaluate(() => window.submissions)).toBe(0);
  });
}

test('reading survey requires verification before Next and dynamically inserted forms are protected', async ({ page }) => {
  await openPage(page, 'assessment.html');
  await expect(page.locator('#next-button')).toBeEnabled();
  await page.locator('#child-name').fill('Test Reader');
  await page.locator('#child-age').selectOption('8');
  await expectVerificationMessage(page, () => page.locator('#next-button').click());
  await expect(page.locator('#step-title')).toHaveText('Let’s meet your reader.');
  await callback(page, 0);
  await page.locator('#next-button').click();
  await expect(page.locator('#step-title')).toHaveText('Sound awareness');
  await page.evaluate(() => {
    state.stepIndex = workflowSteps().length - 1;
    render();
  });
  await expect(page.locator('#step-body form button[type=submit]')).toBeEnabled();
  await expectVerificationMessage(page, () => page.locator('#step-body form button[type=submit]').click());
  expect(await page.evaluate(() => window.submissions)).toBe(0);
  const index = await page.evaluate(() => window.challenges.findIndex(challenge => challenge.el.closest('#step-body form')));
  await callback(page, index);
  await expect(page.locator('#step-body form button[type=submit]')).toBeEnabled();
});

for (const name of ['contact.html', 'support.html', 'careers.html', 'resources.html', '_newsletter_signup.html', 'registration/login.html', 'registration/accept_invitation.html', 'resources/signin.html', 'resources/setup.html', 'crm/newsletter_unsubscribe.html', 'crm/inventory_intake.html', 'crm/inventory_public.html', 'crm/inventory_booking.html', 'crm/consultation_booking.html']) {
  test(`${name}: public form actions explain missing verification`, async ({ page }) => {
    await openPage(page, name);
    const widgets = page.locator('form [data-turnstile-sitekey]');
    expect(await widgets.count()).toBeGreaterThan(0);
    for (const widget of await widgets.all()) {
      const buttons = widget.locator('xpath=ancestor::form').locator('button:not([type]), button[type="submit"], input[type="submit"]');
      expect(await buttons.count()).toBeGreaterThan(0);
      for (const button of await buttons.all()) {
        await expect(button).toBeEnabled();
        await expectVerificationMessage(page, () => button.click());
        await expect(widget).toBeFocused();
        expect(await page.evaluate(() => window.submissions)).toBe(0);
      }
    }
  });
}
