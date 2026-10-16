(() => {
  const form = document.querySelector('[data-reel-generation]');
  if (!form) return;
  const status = form.querySelector('[data-reel-status]');
  const editor = document.getElementById('reel-editor');
  const recovery = form.querySelector('[data-reel-reload]');
  let dirty = false;
  let pending = false;
  editor?.addEventListener('input', () => { dirty = true; });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (pending) return;
    const action = event.submitter?.value || 'refresh';
    if (action === 'start' && dirty) {
      status.textContent = 'Save your storyboard and captions before generating.';
      return;
    }
    pending = true;
    const buttons = [...form.querySelectorAll('button')];
    buttons.forEach(button => { button.disabled = true; });
    status.textContent = action === 'start' ? 'Starting your moving story…' : 'Checking video progress…';
    const data = new FormData(form);
    data.set('action', action);
    try {
      // A button named 'action' shadows HTMLFormElement.action in browsers.
      const endpoint = form.getAttribute('action');
      if (!endpoint) throw new Error('The video request address is missing. Reload this draft before trying again.');
      const response = await fetch(endpoint, { method: 'POST', body: data, credentials: 'same-origin' });
      if (!response.headers.get('content-type')?.includes('application/json')) {
        throw new Error('The server could not confirm the video request. Reload this draft to check its saved progress before trying again.');
      }
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not check the video. Reload to check its saved progress.');
      // Reload the saved draft for fresh revision and generation controls.
      window.location.reload();
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : 'Check your connection, then reload this draft.';
      if (recovery) recovery.hidden = false;
      // A failed response may still have started a paid job; only a reload can safely restore controls.
      if (action === 'refresh') buttons.forEach(button => { button.disabled = false; });
    } finally {
      pending = false;
    }
  });
  if (form.dataset.poll === 'true') {
    window.setTimeout(() => {
      if (!document.hidden && !pending) form.requestSubmit(form.querySelector('button[value="refresh"]'));
    }, 20000);
  }
})();
