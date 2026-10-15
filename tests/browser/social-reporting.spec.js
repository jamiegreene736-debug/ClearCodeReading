const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const pages = JSON.parse(execFileSync(process.env.PYTHON || 'python', ['-c', `
import os, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'clearcodereading.settings')
import django
django.setup()
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse, resolve
from django.core.paginator import Paginator
from django.utils import timezone
from apps.social.models import SocialCategory, SocialPost, SocialPublication, PublicationInsights
from apps.social.reporting import performance_row, summarize
category = SocialCategory(pk=1, name='Free resources', color='#197B80')
post = SocialPost(pk=1, category=category, facebook_caption='A free reading resource for families', status='posted')
post.calendar_at = timezone.now()
pub = SocialPublication(pk=1, post=post, network='facebook', status='published', published_at=timezone.now())
pub.insights = PublicationInsights(metrics={'views':1200,'likes':0}, synced_at=timezone.now())
post._prefetched_objects_cache = {'publications': [pub]}
row = performance_row(pub)
pages = {}
for route, template in [('categories','categories'),('performance','performance'),('post_details','post_details'),('calendar','calendar')]:
    request = RequestFactory().get(reverse('social:'+route, args=[1] if route=='post_details' else []))
    request.resolver_match = resolve(request.path)
    context = {'request':request,'post':post,'categories':[category],'category_choices':[category],'accounts':[], 'snapshots':[], 'rows':[row], 'page':Paginator([post] if route=='categories' else [row],50).page(1), 'summaries':summarize([row]) if route=='performance' else [], 'content_count':1,'publication_count':1,'filters':{'start':'2026-10-01','end':'2026-10-15','period':'lifetime'},'focus':timezone.now().date().replace(day=1),'previous':'2026-09','following':'2026-11','today_month':'2026-10','weekdays':['Sun','Mon','Tue','Wed','Thu','Fri','Sat'],'weeks':[[{'date':timezone.now().date(),'in_month':True,'posts':[post]}]], 'statuses':SocialPost.Status.choices}
    pages[request.path] = render_to_string('social/'+template+'.html',context)
print(json.dumps(pages))
`], { encoding:'utf8' }));
test.use({ channel:'chromium' });
async function open(page, path) {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (pages[url.pathname]) return route.fulfill({ contentType:'text/html',body:pages[url.pathname] });
    const files = {'/static/social/reports.css':'apps/social/static/social/reports.css','/static/social/navigation.css':'apps/social/static/social/navigation.css','/static/css/clearcode-tailwind.css':'apps/core/static/css/clearcode-tailwind.css'};
    if (files[url.pathname]) return route.fulfill({contentType:'text/css',body:fs.readFileSync(files[url.pathname])});
    return route.abort();
  });
  await page.goto('http://report.test'+path);
}
for (const width of [390, 1280]) {
  test(`category, calendar and analytics pages fit at ${width}px`, async ({page}, info) => {
    await page.setViewportSize({width,height:900});
    for (const path of Object.keys(pages)) {
      await open(page,path);
      await expect(page.getByRole('heading').first()).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await page.screenshot({path:info.outputPath(path.replaceAll('/','-')+width+'.png'),fullPage:true});
      await page.unrouteAll();
    }
  });
}
test('category creation and shared colors are explicit on a published post', async ({page}) => {
  await open(page,'/portal/marketing/posts/1/details/');
  await expect(page.locator('#new-category-fields')).toBeHidden();
  await page.locator('#post-category').selectOption('new');
  await expect(page.locator('#category-name')).toBeVisible();
  await expect(page.locator('#category-name')).toHaveAttribute('required','');
  await page.locator('#post-category').selectOption('1');
  await expect(page.locator('#category-color')).toHaveValue('#197b80');
  await expect(page.locator('[name=change_category_color]')).not.toBeChecked();
  await expect(page.getByRole('button',{name:'Save internal category'})).toBeVisible();
  await expect(page.locator('.metric-grid')).toContainText('Unavailable');
  await expect(page.locator('.metric-grid')).toContainText('1200');
});
