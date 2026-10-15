(() => {
  'use strict';
  const form = document.getElementById('social-post-form');
  const preview = form?.querySelector('[data-testid="social-preview"]');
  if (!preview) return;

  const field = (name) => form.elements.namedItem(name);
  const value = (name) => field(name)?.value || '';
  const cards = [...preview.querySelectorAll('[data-preview-network]')];
  const images = [...preview.querySelectorAll('[data-preview-image]')];
  const imageError = preview.querySelector('[data-preview-image-error]');
  let photoSources = [];
  let slideIndex = 0;
  let imageSource = preview.dataset.savedImage || '';
  let imageFailed = false;

  // Python slices Unicode code points when saving, rather than UTF-16 units.
  const limit = (text, length) => [...text].slice(0, length).join('');

  function updateCaption(card, text) {
    const caption = card.querySelector('[data-preview-caption]');
    const expand = card.querySelector('[data-preview-expand]');
    caption.textContent = text || 'Your caption will appear here.';
    caption.classList.add('is-collapsed');
    const overflows = caption.scrollHeight > caption.clientHeight + 1;
    expand.hidden = !overflows;
    if (expand.getAttribute('aria-expanded') === 'true') {
      caption.classList.remove('is-collapsed');
    }
  }

  function render() {
    const manual = value('mode') !== 'brief';
    const facebook = limit(value(manual ? 'caption' : 'facebook_caption'), 5000);
    const instagram = manual
      ? limit(value('instagram_caption').trim() || facebook, 2200)
      : limit(value('instagram_caption'), 2200);
    const link = limit(value('link_url').trim(), 200);
    let domain = '';
    try {
      const parsed = new URL(link);
      if (['https:', 'http:'].includes(parsed.protocol)) domain = parsed.hostname;
    } catch { /* Incomplete URLs stay out of the link card while typing. */ }

    for (const card of cards) {
      const network = card.dataset.previewNetwork;
      card.hidden = !field(`post_to_${network}`).checked;
      updateCaption(card, (network === 'facebook' ? facebook : instagram).trim());
    }
    preview.querySelector('[data-preview-empty]').hidden = cards.some((card) => !card.hidden);
    for (const card of cards) {
      card.querySelector('[data-carousel-controls]').hidden = photoSources.length < 2;
      card.querySelector('[data-slide-count]').textContent = `${slideIndex + 1} / ${photoSources.length}`;
    }
    for (const image of images) {
      image.hidden = !imageSource || imageFailed;
      if (imageSource && image.getAttribute('src') !== imageSource) image.src = imageSource;
      if (!imageSource) image.removeAttribute('src');
    }
    preview.querySelector('[data-preview-photo-needed]').hidden = Boolean(imageSource) && !imageFailed;
    preview.querySelector('[data-preview-link]').hidden = !domain || Boolean(imageSource);
    preview.querySelector('[data-preview-domain]').textContent = domain;
    preview.querySelector('[data-preview-url]').textContent = link;
    preview.querySelector('[data-preview-photo-link]').hidden = !link || !imageSource;
  }

  for (const image of images) {
    image.addEventListener('error', () => {
      imageFailed = true;
      imageError.textContent = 'The photo could not be previewed. Choose another photo or reload the saved draft.';
      imageError.hidden = false;
      render();
    });
  }
  for (const card of cards) {
    const expand = card.querySelector('[data-preview-expand]');
    expand.addEventListener('click', () => {
      const expanded = expand.getAttribute('aria-expanded') !== 'true';
      expand.setAttribute('aria-expanded', String(expanded));
      expand.textContent = expanded ? 'See less' : 'See more';
      render();
    });
  }
  form.addEventListener('input', (event) => { if (event.target !== field('image')) render(); });
  form.addEventListener('change', render);
  window.addEventListener('resize', render);
  window.addEventListener('pageshow', render);
  for (const card of cards) {
    const move = (direction) => {
      if (!photoSources.length) return;
      slideIndex = (slideIndex + direction + photoSources.length) % photoSources.length;
      imageSource = photoSources[slideIndex];
      imageFailed = false;
      imageError.hidden = true;
      render();
    };
    card.querySelector('[data-slide-prev]').addEventListener('click', () => move(-1));
    card.querySelector('[data-slide-next]').addEventListener('click', () => move(1));
  }
  form.addEventListener('photoschange', (event) => {
    photoSources = event.detail.sources;
    slideIndex = Math.min(slideIndex, Math.max(0, photoSources.length - 1));
    imageSource = photoSources[slideIndex] || '';
    imageFailed = false;
    imageError.hidden = !event.detail.error;
    if (event.detail.error) imageError.textContent = event.detail.error;
    render();
  });
  render();
})();

(() => {
  'use strict';
  const form = document.getElementById('social-post-form');
  const editor = form?.querySelector('[data-carousel-editor]');
  if (!editor) return;
  const input = form.elements.images;
  const replacement = form.elements.image;
  const saved = [...editor.querySelectorAll('[data-saved-photos] span')].map((node) => ({ ...node.dataset }));
  let photos = [...saved];
  let dirty = false;
  const urls = new Set();
  const error = editor.querySelector('[data-photo-error]');
  const makePhoto = (file) => {
    const url = URL.createObjectURL(file);
    urls.add(url);
    return { file, url, name: file.name };
  };
  function sync() {
    const transfer = new DataTransfer();
    const keys = [];
    let message = photos.length > 10 ? 'Choose up to 10 photos.' : '';
    for (const photo of photos) {
      if (photo.file) {
        keys.push(`upload:${transfer.files.length}`);
        transfer.items.add(photo.file);
        if (!['image/jpeg', 'image/png', 'image/webp'].includes(photo.file.type)) message = 'Choose a JPEG, PNG, or WebP photo to preview this draft.';
        if (photo.file.size > 8 * 1024 * 1024) message = 'Each photo must be under 8 MB.';
      } else keys.push(photo.key);
    }
    input.files = transfer.files;
    form.elements.photo_order.disabled = false;
    form.elements.photo_order.value = JSON.stringify(keys);
    form.dataset.hasImage = String(photos.length > 0);
    form.dataset.photoError = message;
    error.textContent = message;
    error.hidden = !message;
    const list = editor.querySelector('[data-photo-list]');
    list.replaceChildren();
    photos.forEach((photo, index) => {
      const item = document.createElement('li');
      const image = document.createElement('img');
      image.src = photo.url;
      image.alt = `Photo ${index + 1}: ${photo.name}`;
      const label = document.createElement('span');
      label.textContent = `${index + 1}. ${photo.name}`;
      item.append(image, label);
      for (const [text, delta] of [['Earlier', -1], ['Later', 1], ['Remove', 0]]) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = text;
        button.setAttribute('aria-label', `${text} photo ${index + 1}`);
        button.disabled = delta !== 0 && (index + delta < 0 || index + delta >= photos.length);
        button.addEventListener('click', () => {
          if (!delta) photos.splice(index, 1);
          else [photos[index], photos[index + delta]] = [photos[index + delta], photos[index]];
          dirty = true;
          sync();
          form.dispatchEvent(new Event('input', { bubbles: true }));
          const moved = list.children[Math.max(0, Math.min(photos.length - 1, index + delta))];
          moved?.querySelector('button:not(:disabled)')?.focus();
        });
        item.append(button);
      }
      list.append(item);
    });
    form.dispatchEvent(new CustomEvent('photoschange', { detail: { sources: message ? [] : photos.map((photo) => photo.url), error: message } }));
    const activeUrls = new Set(photos.map((photo) => photo.url));
    for (const url of urls) {
      if (!activeUrls.has(url)) { URL.revokeObjectURL(url); urls.delete(url); }
    }
  }
  input.addEventListener('change', () => {
    photos = photos.concat([...input.files].map(makePhoto));
    replacement.value = '';
    dirty = true;
    sync();
  });
  replacement.addEventListener('change', () => {
    photos = replacement.files.length ? [makePhoto(replacement.files[0])] : [...saved];
    dirty = true;
    sync();
  });
  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('change', () => { dirty = true; });
  form.addEventListener('submit', (event) => {
    if (form.dataset.photoError) event.preventDefault();
  });
  const generate = editor.querySelector('[data-generate-slides]');
  generate?.addEventListener('click', async () => {
    const progress = editor.querySelector('[data-generation-progress]');
    if (dirty) {
      progress.textContent = 'Save your draft before generating slides.';
      return;
    }
    const controls = [...form.querySelectorAll('button, input, select, textarea')];
    const states = controls.map((control) => control.disabled);
    controls.forEach((control) => { control.disabled = true; });
    form.dataset.busy = 'true';
    form.setAttribute('aria-busy', 'true');
    const csrf = form.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
    try {
      while (true) {
        progress.textContent = 'Generating the next slide. This can take a few minutes…';
        const response = await fetch(generate.dataset.url, { method: 'POST', headers: { 'X-CSRFToken': csrf, Accept: 'application/json' }, credentials: 'same-origin' });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Generation stopped. Resume to try the remaining slides.');
        progress.textContent = `${result.completed} of ${result.total} slides saved.`;
        const meter = editor.querySelector('[data-slide-progress]');
        meter.max = result.total;
        meter.value = result.completed;
        if (result.completed >= result.total) { window.location.reload(); return; }
      }
    } catch (failure) {
      progress.textContent = `${failure.message || 'Generation stopped.'} Completed slides are saved. Resume to continue, or reload to view them.`;
    } finally {
      controls.forEach((control, index) => { control.disabled = states[index]; });
      form.dataset.busy = 'false';
      form.removeAttribute('aria-busy');
    }
  });
  window.addEventListener('pagehide', (event) => {
    if (!event.persisted) urls.forEach((url) => URL.revokeObjectURL(url));
  });
  sync();
})();
