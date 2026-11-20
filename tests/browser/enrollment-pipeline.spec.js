const { expect, test } = require('@playwright/test');
const { execFileSync } = require('node:child_process');

const html = execFileSync(process.env.PYTHON || 'python', ['-c', `
import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'clearcodereading.settings')
import django
django.setup()
from django.template.loader import render_to_string
from apps.crm.models import Opportunity
from apps.crm.views import _stage_columns

pipeline = Opportunity.Pipeline.FAMILY_ENROLLMENT
choices = Opportunity.stage_choices_for_pipeline(pipeline)
deals = [
    SimpleNamespace(pk=index, name=f'Enrollment {index}', stage=choices[stage][0],
                    stage_choices=choices, deal_label='Enrollment', value=1500,
                    owner={'email': 'family' * 10 + '@example.org'},
                    next_steps='https://example.org/' + 'a' * 110)
    for index, stage in enumerate([0, 0, 1], start=1)
]
with patch('apps.crm.templatetags.crm_display.pending_routing_people_count', return_value=0), \\
     patch('apps.crm.templatetags.crm_display.pending_completion_people_count', return_value=0):
    print(render_to_string('crm/deal_list.html', {
        'view': 'board', 'pipeline': pipeline, 'pipeline_label': pipeline.label,
        'stage_columns': _stage_columns(pipeline, deals),
        'pipeline_tabs': [{'value': pipeline, 'label': pipeline.label, 'deal_count': len(deals)}],
    }))
`], { encoding: 'utf8' });

for (const width of [320, 390, 768, 1280]) {
  test(`enrollment cards stay inside their stage at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route('**/*', route => route.abort());
    await page.setContent(html);
    const cards = page.locator('.deal-card');
    await expect(cards).toHaveCount(3);
    const overflow = await page.locator('.board, .stage, .deal-card').evaluateAll(elements =>
      elements.filter(el => el.scrollWidth > el.clientWidth + 1).map(el => el.id || el.className)
    );
    expect(overflow).toEqual([]);
    const stages = await page.locator('.stage').evaluateAll(elements =>
      elements.map(el => el.getBoundingClientRect().toJSON())
    );
    expect(stages.at(-1).top).toBeGreaterThan(stages[0].top);
    if (width <= 760) expect(stages[1].top).toBeGreaterThanOrEqual(stages[0].bottom);
    if (width === 1280) expect(stages[1].top).toBe(stages[0].top);
    const first = await cards.nth(0).boundingBox();
    const second = await cards.nth(1).boundingBox();
    expect(second.y).toBeGreaterThanOrEqual(first.y + first.height);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}
