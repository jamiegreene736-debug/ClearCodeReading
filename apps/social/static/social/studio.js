(() => {
  'use strict';
  const config = document.getElementById('studio-data');
  if (!config) return;
  const settings = JSON.parse(config.textContent);
  const root = document.getElementById('studio');
  const $ = id => document.getElementById(id);
  let design = settings.document, pageIndex = 0, selected = 0, dirty = false, busy = false;
  const undo = [], redo = [], images = new Map();
  const canvas = $('studio-canvas');
  const fonts = {sans: 'Arial, sans-serif', serif: 'Georgia, serif', condensed: 'Impact, "Arial Narrow", sans-serif'};
  const fields = ['text', 'font', 'weight', 'size', 'color', 'align', 'strike', 'x', 'y', 'width', 'height'];
  const numeric = new Set(['size', 'x', 'y', 'width', 'height']);
  const page = () => design.pages[pageIndex];
  const element = () => page().elements[selected];
  const snapshot = () => JSON.stringify({design, pageIndex, selected});
  function status(text, error = false) { $('studio-status').textContent = text; $('studio-status').dataset.error = String(error); }
  function record() { undo.push(snapshot()); if (undo.length > 35) undo.shift(); redo.length = 0; dirty = true; }
  function edit(callback) { if (busy) return; record(); callback(); refresh(); }
  function loadImage(source) {
    if (!images.has(source)) images.set(source, new Promise((resolve, reject) => {
      const image = new Image(); image.onload = () => resolve(image); image.onerror = () => reject(new Error('A background image could not be loaded. Remove it or upload it again.')); image.src = source;
    }));
    return images.get(source);
  }
  function linesFor(context, text, width) {
    const lines = [];
    for (const paragraph of text.split('\n')) {
      let line = '';
      for (const word of paragraph.split(/\s+/)) {
        const proposed = line ? line + ' ' + word : word;
        if (context.measureText(proposed).width <= width) { line = proposed; continue; }
        if (line) { lines.push(line); line = ''; }
        for (const character of word) {
          if (line && context.measureText(line + character).width > width) { lines.push(line); line = ''; }
          line += character;
        }
      }
      lines.push(line);
    }
    return lines;
  }
  async function draw(target, sheet, selection = -1) {
    target.width = design.width; target.height = design.height;
    const context = target.getContext('2d');
    context.fillStyle = sheet.background; context.fillRect(0, 0, target.width, target.height);
    if (sheet.image) {
      const image = await loadImage(sheet.image);
      const scale = Math.max(target.width / image.width, target.height / image.height);
      context.drawImage(image, (target.width - image.width * scale) / 2, (target.height - image.height * scale) / 2, image.width * scale, image.height * scale);
    }
    const overflow = [];
    sheet.elements.forEach((item, index) => {
      context.fillStyle = item.color;
      if (item.kind === 'box') context.fillRect(item.x, item.y, item.width, item.height);
      else {
        context.font = `${item.weight} ${item.size}px ${fonts[item.font]}`;
        context.textBaseline = 'top'; context.textAlign = item.align;
        const lines = linesFor(context, item.text, item.width), leading = item.size * 1.18;
        if (lines.length * leading > item.height + 1 || item.x + item.width > target.width + 1 || item.y + item.height > target.height + 1) overflow.push(index + 1);
        const x = item.x + (item.align === 'center' ? item.width / 2 : item.align === 'right' ? item.width : 0);
        lines.forEach((line, lineIndex) => {
          const y = item.y + lineIndex * leading;
          context.fillText(line, x, y);
          if (item.strike) {
            const width = context.measureText(line).width;
            const left = x - (item.align === 'center' ? width / 2 : item.align === 'right' ? width : 0);
            context.fillRect(left, y + item.size * .52, width, Math.max(2, item.size / 22));
          }
        });
      }
      if (selection === index) {
        context.save(); context.strokeStyle = '#f3ac2c'; context.lineWidth = 3; context.setLineDash([10, 7]);
        context.strokeRect(item.x, item.y, item.width, item.height); context.restore();
      }
    });
    return overflow;
  }
  let paint = 0;
  async function preview() {
    const current = ++paint, buffer = document.createElement('canvas');
    try {
      const overflow = await draw(buffer, page(), selected);
      if (current !== paint) return;
      canvas.width = buffer.width; canvas.height = buffer.height; canvas.getContext('2d').drawImage(buffer, 0, 0);
      $('studio-overflow').textContent = overflow.length ? `Text box ${overflow.join(', ')} needs more room. Shorten the words, reduce the text size, or enlarge the box before exporting.` : '';
    } catch (error) { status(error.message, true); }
  }
  function controls() {
    const item = element(); $('studio-properties').disabled = !item || busy;
    if (item) fields.forEach(field => { const input = $('element-' + field); if (field === 'strike') input.checked = item[field]; else input.value = item[field]; });
    $('element-text').disabled = !item || item.kind !== 'text';
    $('studio-undo').disabled = !undo.length || busy; $('studio-redo').disabled = !redo.length || busy;
    $('studio-page-remove').disabled = design.pages.length === 1 || busy;
    $('studio-page-before').disabled = pageIndex === 0 || busy; $('studio-page-after').disabled = pageIndex === design.pages.length - 1 || busy;
    $('studio-page-add').disabled = design.pages.length === 10 || busy; $('studio-page-copy').disabled = design.pages.length === 10 || busy;
  }
  function refresh() {
    pageIndex = Math.min(pageIndex, design.pages.length - 1); selected = Math.min(selected, page().elements.length - 1);
    $('studio-size').value = design.height; $('studio-background').value = page().background;
    $('studio-pages').replaceChildren(...design.pages.map((_, i) => {
      const button = document.createElement('button'); button.textContent = `Page ${i + 1}`; button.setAttribute('aria-current', String(i === pageIndex));
      button.onclick = () => { pageIndex = i; selected = 0; refresh(); }; return button;
    }));
    $('studio-elements').replaceChildren(...page().elements.map((item, i) => {
      const option = document.createElement('option'); option.value = i; option.textContent = `${i + 1}. ${item.kind === 'box' ? 'Color block' : item.text.slice(0, 40) || 'Empty text'}`; return option;
    }));
    $('studio-elements').value = selected; controls(); preview();
  }
  fields.forEach(field => $('element-' + field).addEventListener('input', event => {
    if (!element() || busy) return;
    const input = event.target;
    let value = field === 'strike' ? input.checked : numeric.has(field) ? Number(input.value) : input.value;
    if (numeric.has(field) && (!Number.isFinite(value) || value < Number(input.min) || value > Number(input.max))) return;
    record(); element()[field] = value; preview();
    $('studio-undo').disabled = false;
  }));
  $('studio-elements').onchange = event => { selected = Number(event.target.value); controls(); preview(); };
  $('studio-background').oninput = event => edit(() => { page().background = event.target.value; });
  $('studio-size').onchange = event => edit(() => { design.height = Number(event.target.value); });
  $('studio-template').onchange = event => { if (event.target.value) location.href = location.pathname + '?' + event.target.value; };
  $('studio-page-add').onclick = () => edit(() => { design.pages.push({background: '#F7F2EA', image: '', elements: []}); pageIndex = design.pages.length - 1; selected = -1; });
  $('studio-page-copy').onclick = () => edit(() => { design.pages.splice(pageIndex + 1, 0, structuredClone(page())); pageIndex++; });
  $('studio-page-remove').onclick = () => { if (design.pages.length > 1) edit(() => { design.pages.splice(pageIndex, 1); }); };
  for (const [id, offset] of [['studio-page-before', -1], ['studio-page-after', 1]]) $(id).onclick = () => edit(() => { const [sheet] = design.pages.splice(pageIndex, 1); pageIndex += offset; design.pages.splice(pageIndex, 0, sheet); });
  function add(kind) {
    if (page().elements.length >= 32) { status('A page can have up to 32 elements.', true); return; }
    edit(() => { page().elements.push({kind, text: kind === 'text' ? 'Your words here' : '', x: 80, y: 100, width: 800, height: 200, size: 64, font: 'sans', weight: 'bold', align: 'left', strike: false, color: kind === 'text' ? '#102F38' : '#CCDCD5'}); selected = page().elements.length - 1; });
  }
  $('studio-text-add').onclick = () => add('text'); $('studio-box-add').onclick = () => add('box');
  $('studio-element-copy').onclick = () => { if (element() && page().elements.length < 32) edit(() => { page().elements.push(structuredClone(element())); selected = page().elements.length - 1; }); };
  $('studio-element-remove').onclick = () => edit(() => { if (element()) page().elements.splice(selected, 1); });
  for (const [id, offset] of [['studio-front', 1], ['studio-back', -1]]) $(id).onclick = () => { const next = selected + offset; if (element() && next >= 0 && next < page().elements.length) edit(() => { const [item] = page().elements.splice(selected, 1); page().elements.splice(next, 0, item); selected = next; }); };
  function restore(from, to) { if (!from.length || busy) return; to.push(snapshot()); const state = JSON.parse(from.pop()); design = state.design; pageIndex = state.pageIndex; selected = state.selected; dirty = true; refresh(); }
  $('studio-undo').onclick = () => restore(undo, redo); $('studio-redo').onclick = () => restore(redo, undo);
  let drag = null;
  function point(event) { const rect = canvas.getBoundingClientRect(); return {x: (event.clientX - rect.left) * design.width / rect.width, y: (event.clientY - rect.top) * design.height / rect.height}; }
  canvas.onpointerdown = event => {
    if (busy) return; const p = point(event);
    selected = page().elements.findLastIndex(item => p.x >= item.x && p.x <= item.x + item.width && p.y >= item.y && p.y <= item.y + item.height);
    if (element()) { record(); drag = {x: p.x - element().x, y: p.y - element().y}; canvas.setPointerCapture(event.pointerId); }
    refresh();
  };
  canvas.onpointermove = event => { if (!drag || !element()) return; const p = point(event); element().x = Math.round(Math.max(0, Math.min(design.width - element().width, p.x - drag.x))); element().y = Math.round(Math.max(0, Math.min(design.height - element().height, p.y - drag.y))); controls(); preview(); };
  canvas.onpointerup = canvas.onpointercancel = () => { drag = null; };
  $('studio-upload').onchange = async event => {
    const file = event.target.files[0]; if (!file) return;
    try {
      if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 8 * 1024 * 1024) throw new Error('Choose a PNG, JPEG, or WebP smaller than 8 MB.');
      const source = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); });
      const image = await loadImage(source), output = document.createElement('canvas');
      if (image.width * image.height > 16777216) throw new Error('Use a background smaller than 16 megapixels.');
      const scale = Math.min(1, 1440 / Math.max(image.width, image.height)); output.width = Math.round(image.width * scale); output.height = Math.round(image.height * scale);
      output.getContext('2d').drawImage(image, 0, 0, output.width, output.height);
      edit(() => { page().image = output.toDataURL('image/png'); }); status('Background added. Text in an uploaded image cannot be edited.');
    } catch (error) { status(error.message || 'Could not read this image.', true); }
    event.target.value = '';
  };
  $('studio-clear-background').onclick = () => edit(() => { page().image = ''; });
  async function exportPage(sheet) {
    const output = document.createElement('canvas');
    if ((await draw(output, sheet)).length) throw new Error('Some text needs more room. Check each page for an overflow warning before saving or exporting.');
    return new Promise((resolve, reject) => output.toBlob(blob => blob ? resolve(blob) : reject(new Error('Could not export the image.')), 'image/png'));
  }
  async function run(callback) {
    if (busy) return; busy = true;
    const disabled = [...root.querySelectorAll('button,input,select,textarea')].map(input => [input, input.disabled]);
    disabled.forEach(([input]) => { input.disabled = true; }); status('Preparing your artwork…');
    try { await callback(); } catch (error) { status(error.message || 'Could not save. Your design is still here; please try again.', true); }
    finally { busy = false; disabled.forEach(([input, value]) => { input.disabled = value; }); controls(); }
  }
  async function send(url, data) {
    const token = document.querySelector('#studio-token [name=csrfmiddlewaretoken]');
    if (!token) throw new Error('Your session could not be verified. Sign in and reopen the editor.');
    const response = await fetch(url, {method: 'POST', body: data, headers: {'X-CSRFToken': token.value}});
    let result; try { result = await response.json(); } catch { throw new Error('The save failed or your session expired. Your design is still here. Try again after signing in.'); }
    if (!response.ok) throw new Error(result.error || 'Could not save this design.'); return result;
  }
  async function exports() { const blobs = []; for (let i = 0; i < design.pages.length; i++) { status(`Preparing page ${i + 1} of ${design.pages.length}…`); blobs.push(await exportPage(design.pages[i])); } return blobs; }
  $('studio-download').onclick = () => run(async () => { const blob = await exportPage(page()), url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `clearcode-page-${pageIndex + 1}.png`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); status('Image downloaded. Your editable design is unchanged.'); });
  async function save(copy) {
    if (!$('studio-name').value.trim()) { status('Give your template a name first.', true); $('studio-name').focus(); return; }
    await run(async () => {
      const blobs = await exports(), data = new FormData(); data.set('document', JSON.stringify(design)); data.set('name', $('studio-name').value.trim()); data.set('preview', blobs[0], 'preview.png');
      if (!copy && settings.templateId) { data.set('template_id', settings.templateId); data.set('revision', settings.templateRevision); }
      const result = await send(settings.saveUrl, data); settings.templateId = result.id; settings.templateRevision = result.revision; $('studio-save').hidden = false; dirty = false; status('Template saved. You can reuse it from Image templates.');
    });
  }
  $('studio-save-new').onclick = () => save(true); $('studio-save').onclick = () => save(false);
  $('studio-apply').onclick = () => run(async () => {
    const blobs = await exports(), data = new FormData(); data.set('document', JSON.stringify(design)); data.set('mode', $('studio-apply-mode').value);
    if (settings.postId) { data.set('post_id', settings.postId); data.set('revision', settings.postRevision); }
    blobs.forEach((blob, i) => data.append('images', blob, `page-${i + 1}.png`));
    const result = await send(settings.applyUrl, data); dirty = false; location.assign(result.url);
  });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  refresh();
})();
