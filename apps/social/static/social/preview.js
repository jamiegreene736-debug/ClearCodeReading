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
  let objectUrl = '';
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

  function changePhoto() {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = '';
    imageFailed = false;
    imageError.hidden = true;
    const file = field('image').files[0];
    imageSource = preview.dataset.savedImage || '';
    if (file) {
      if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
        imageSource = '';
        imageError.textContent = 'Choose a JPEG, PNG, or WebP photo to preview this draft.';
        imageError.hidden = false;
      } else {
        objectUrl = URL.createObjectURL(file);
        imageSource = objectUrl;
      }
    }
    render();
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
  form.addEventListener('change', (event) => { if (event.target === field('image')) changePhoto(); else render(); });
  window.addEventListener('resize', render);
  window.addEventListener('pageshow', render);
  window.addEventListener('pagehide', (event) => {
    if (!event.persisted && objectUrl) URL.revokeObjectURL(objectUrl);
  });
  render();
})();
