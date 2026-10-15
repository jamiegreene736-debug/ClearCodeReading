const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const html = execFileSync(process.env.PYTHON || 'python', ['-c', `
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','clearcodereading.settings')
import django
django.setup()
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import resolve, reverse
from apps.social.designs import starter
request=RequestFactory().get(reverse('social:design_editor'))
request.resolver_match=resolve(request.path)
print(render_to_string('social/design_editor.html', {'request':request,'csrf_token':'browser-test-token','studio':{'document':starter(),'templateId':None,'templateRevision':'','postId':None,'postRevision':'','saveUrl':reverse('social:design_save'),'applyUrl':reverse('social:design_apply')},'templates':[]}))
`], {encoding:'utf8'});
test.use({channel:'chromium'});
async function open(page) {
  await page.route('**/*', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/studio') return route.fulfill({contentType:'text/html',body:html});
    const assets = {'/static/social/studio.js':'apps/social/static/social/studio.js','/static/social/studio.css':'apps/social/static/social/studio.css','/static/social/navigation.css':'apps/social/static/social/navigation.css','/static/social/reports.css':'apps/social/static/social/reports.css','/static/css/clearcode-tailwind.css':'apps/core/static/css/clearcode-tailwind.css'};
    if (assets[path]) return route.fulfill({contentType:path.endsWith('.js')?'text/javascript':'text/css',body:fs.readFileSync(assets[path])});
    return route.abort();
  });
  await page.goto('http://studio.test/studio');
  await expect(page.locator('#studio-pages button')).toHaveCount(2);
  await expect(page.locator('#studio-overflow')).toBeEmpty();
}
for (const width of [390,1280]) {
  test(`template editor fits ${width}px and exports its artwork`, async ({page}, info) => {
    await page.setViewportSize({width,height:1000}); await open(page);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({path:info.outputPath(`studio-${width}.png`),fullPage:true});
    await page.locator('#studio-elements').selectOption('2');
    await page.locator('#element-text').fill('We can read\ntogether.');
    await expect(page.locator('#studio-overflow')).toBeEmpty();
    const download = page.waitForEvent('download'); await page.locator('#studio-download').click();
    expect((await download).suggestedFilename()).toBe('clearcode-page-1.png');
    await expect(page.locator('#studio-status')).toContainText('Image downloaded');
    await page.locator('#studio-pages button').nth(1).click();
    await expect(page.locator('#studio-overflow')).toBeEmpty();
    await page.screenshot({path:info.outputPath(`studio-second-${width}.png`),fullPage:true});
  });
}
test('page edits support undo, reordering, save and attaching every page', async ({page}) => {
  await open(page);
  await page.locator('#studio-page-copy').click(); await expect(page.locator('#studio-pages button')).toHaveCount(3);
  await page.locator('#studio-undo').click(); await expect(page.locator('#studio-pages button')).toHaveCount(2);
  await page.locator('#studio-redo').click(); await expect(page.locator('#studio-pages button')).toHaveCount(3);
  await page.locator('#studio-page-after').click();
  await page.locator('#studio-name').fill('Reusable scripts');
  let saved = '', attached = '';
  await page.route('**/designs/save/', async route => {saved=route.request().postData(); await route.fulfill({json:{id:9,revision:'new'}});});
  await page.locator('#studio-save-new').click();
  await expect(page.locator('#studio-status')).toContainText('Template saved');
  expect(saved).toContain('Reusable scripts'); expect(saved).toContain('preview.png');
  await page.route('**/designs/apply/', async route => {attached=route.request().postData(); await route.fulfill({json:{url:'/done'}});});
  await page.route('**/done', route=>route.fulfill({body:'Draft artwork saved'}));
  await page.locator('#studio-apply').click(); await expect(page).toHaveURL(/done$/);
  for (const file of ['page-1.png','page-2.png','page-3.png']) expect(attached).toContain(file);
});
test('overflow blocks export and failed saves keep editable text', async ({page}) => {
  await open(page); await page.locator('#studio-elements').selectOption('2');
  await page.locator('#element-text').fill('Many words '.repeat(150));
  await expect(page.locator('#studio-overflow')).toContainText('needs more room');
  await page.locator('#studio-download').click(); await expect(page.locator('#studio-status')).toContainText('needs more room');
  await page.locator('#element-text').fill('A calm moment.'); await page.locator('#studio-name').fill('My template');
  await page.route('**/designs/save/', route=>route.fulfill({status:400,json:{error:'This template changed in another window.'}}));
  await page.locator('#studio-save-new').click(); await expect(page.locator('#studio-status')).toContainText('changed in another window');
  await expect(page.locator('#element-text')).toHaveValue('A calm moment.');
});
test('uploaded backgrounds render and text can be dragged', async ({page}) => {
  await open(page);
  const encoded = await page.evaluate(() => {const c=document.createElement('canvas');c.width=c.height=4;const ctx=c.getContext('2d');ctx.fillStyle='#ee2222';ctx.fillRect(0,0,4,4);return c.toDataURL('image/png').split(',')[1];});
  await page.locator('#studio-upload').setInputFiles({name:'background.png',mimeType:'image/png',buffer:Buffer.from(encoded,'base64')});
  await expect(page.locator('#studio-status')).toContainText('Background added');
  await expect.poll(() => page.locator('#studio-canvas').evaluate(c=>c.getContext('2d').getImageData(1070,1070,1,1).data[0])).toBeGreaterThan(220);
  await page.locator('#studio-canvas').scrollIntoViewIfNeeded();
  const box=await page.locator('#studio-canvas').boundingBox();
  const x=box.x+100*box.width/1080, y=box.y+410*box.height/1080;
  await page.mouse.move(x,y); await page.mouse.down(); await page.mouse.move(x+20,y+20); await page.mouse.up();
  expect(Number(await page.locator('#element-x').inputValue())).toBeGreaterThan(72);
  await page.locator('#studio-clear-background').click();
  await expect.poll(() => page.locator('#studio-canvas').evaluate(c=>c.getContext('2d').getImageData(1070,1070,1,1).data[0])).toBeLessThan(40);
});
