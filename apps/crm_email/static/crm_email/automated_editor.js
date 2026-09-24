/* Rich editor for automated email wording.
 * Attaches to textarea[data-rich-editor="automated"]; config comes from #automated-editor-config.
 * Output is HTML that the server sanitizes again (clean_rich_html) before saving. */
(() => {
  const configNode = document.getElementById('automated-editor-config');
  if (!configNode) return;
  const config = JSON.parse(configNode.textContent || '{}');
  const placeholders = config.placeholders || {};
  const sample = config.sample || {};
  const csrf = (document.querySelector('input[name="csrfmiddlewaretoken"]') || {}).value || '';

  const BLOCKS = new Set(['P', 'DIV', 'BR', 'HR', 'SPAN', 'STRONG', 'B', 'EM', 'I', 'U', 'S', 'STRIKE', 'SUB', 'SUP',
    'H1', 'H2', 'H3', 'H4', 'UL', 'OL', 'LI', 'BLOCKQUOTE', 'A', 'IMG', 'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD']);
  const STYLE_PROPS = new Set(['color', 'background-color', 'font-size', 'font-family', 'font-weight', 'font-style',
    'text-decoration', 'text-align', 'line-height', 'margin', 'margin-top', 'margin-bottom', 'padding', 'border-radius',
    'width', 'max-width', 'height', 'display']);
  const IMAGE_MAX_PIXEL_WIDTH = 560;

  function safeStyle(value) {
    return (value || '').split(';').map(part => part.trim()).filter(Boolean).map(part => {
      const index = part.indexOf(':');
      if (index < 0) return '';
      const name = part.slice(0, index).trim().toLowerCase();
      const val = part.slice(index + 1).trim();
      if (!STYLE_PROPS.has(name) || /url\(|expression|javascript/i.test(val)) return '';
      return name + ':' + val;
    }).filter(Boolean).join(';');
  }

  function safeFragment(value) {
    const parsed = new DOMParser().parseFromString(value, 'text/html');
    function copy(node) {
      if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent);
      if (node.nodeType !== Node.ELEMENT_NODE) return document.createDocumentFragment();
      if (['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'SVG', 'MATH'].includes(node.tagName)) return document.createDocumentFragment();
      const result = BLOCKS.has(node.tagName) ? document.createElement(node.tagName.toLowerCase()) : document.createDocumentFragment();
      if (result.nodeType === Node.ELEMENT_NODE) {
        const style = safeStyle(node.getAttribute('style'));
        if (style) result.setAttribute('style', style);
        if (node.tagName === 'A') {
          const href = node.getAttribute('href') || '';
          if (/^(https?:\/\/|mailto:)/i.test(href)) result.setAttribute('href', href);
        }
        if (node.tagName === 'IMG') {
          const src = node.getAttribute('src') || '';
          if (!/^https?:\/\//i.test(src)) return document.createDocumentFragment();
          result.setAttribute('src', src);
          result.setAttribute('alt', node.getAttribute('alt') || '');
          for (const attr of ['width', 'height']) {
            const v = node.getAttribute(attr);
            if (v && /^\d+$/.test(v)) result.setAttribute(attr, v);
          }
        }
        if (['TD', 'TH'].includes(node.tagName)) {
          for (const attr of ['align', 'valign', 'width', 'colspan']) {
            const v = node.getAttribute(attr);
            if (v && /^[\w%]+$/.test(v)) result.setAttribute(attr, v);
          }
        }
      }
      for (const child of node.childNodes) result.append(copy(child));
      return result;
    }
    const fragment = document.createDocumentFragment();
    for (const node of parsed.body.childNodes) fragment.append(copy(node));
    return fragment;
  }

  function escapeHtml(text) {
    return text.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function fillSampleText(text) {
    return text.replace(/{{\s*([^{}]+?)\s*}}/g, (match, token) => (sample[token] === undefined ? match : sample[token]));
  }

  function fillSample(html) {
    return html.replace(/{{\s*([^{}]+?)\s*}}/g, (match, token) => {
      const value = sample[token];
      if (value === undefined) return '<mark>' + escapeHtml(match) + '</mark>';
      return escapeHtml(value).replace(/\n/g, '<br>');
    });
  }

  function makeButton(label, title, onClick, extraClass) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-small editor-tool' + (extraClass ? ' ' + extraClass : '');
    button.innerHTML = label;
    button.title = title;
    button.setAttribute('aria-label', title);
    button.addEventListener('mousedown', event => event.preventDefault());
    button.addEventListener('click', onClick);
    return button;
  }

  function makeSelect(title, options, onChange) {
    const select = document.createElement('select');
    select.className = 'editor-select';
    select.title = title;
    select.setAttribute('aria-label', title);
    for (const [value, label] of options) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      select.append(option);
    }
    select.addEventListener('mousedown', () => { select.dataset.range = '1'; });
    select.addEventListener('change', () => { onChange(select.value); select.selectedIndex = 0; });
    return select;
  }

  function makeColor(title, icon, onPick) {
    const wrap = document.createElement('label');
    wrap.className = 'btn btn-small editor-tool editor-color';
    wrap.title = title;
    wrap.innerHTML = icon;
    const input = document.createElement('input');
    input.type = 'color';
    input.setAttribute('aria-label', title);
    input.addEventListener('input', () => onPick(input.value));
    wrap.append(input);
    return wrap;
  }

  for (const textarea of document.querySelectorAll('textarea[data-rich-editor="automated"]')) {
    const shell = document.createElement('div');
    shell.className = 'automated-editor';

    const editor = document.createElement('div');
    editor.className = 'rich-editor automated-editor-surface';
    editor.id = textarea.id + '_editor';
    editor.contentEditable = 'true';
    editor.setAttribute('role', 'textbox');
    editor.setAttribute('aria-multiline', 'true');
    editor.setAttribute('aria-label', textarea.getAttribute('aria-label') || 'Message');
    editor.append(safeFragment(textarea.value));

    const source = document.createElement('textarea');
    source.className = 'automated-editor-source';
    source.hidden = true;
    source.rows = 14;
    source.setAttribute('aria-label', 'HTML source');
    source.spellcheck = false;

    const status = document.createElement('p');
    status.className = 'subtle editor-status';
    status.setAttribute('role', 'status');

    let savedRange = null;
    let pendingSize = '';
    function rememberSelection() {
      const selection = window.getSelection();
      if (selection && selection.rangeCount && editor.contains(selection.anchorNode)) savedRange = selection.getRangeAt(0).cloneRange();
    }
    function restoreSelection() {
      editor.focus();
      const selection = window.getSelection();
      if (savedRange && selection) { selection.removeAllRanges(); selection.addRange(savedRange); }
    }
    function normalizeSizes() {
      // execCommand('fontSize', 7) marks text as xxx-large; swap in the pixel size that was chosen.
      for (const el of editor.querySelectorAll('font[size], span[style*="xxx-large"]')) {
        const span = document.createElement('span');
        if (el.getAttribute('style')) span.setAttribute('style', el.getAttribute('style'));
        span.style.fontSize = pendingSize || '16px';
        span.append(...el.childNodes);
        el.replaceWith(span);
      }
    }
    function sync() {
      if (!source.hidden) {
        editor.replaceChildren(safeFragment(source.value));
      }
      normalizeSizes();
      textarea.value = editor.innerHTML;
      editor.dispatchEvent(new CustomEvent('automated-editor:change', { bubbles: true }));
    }
    function exec(command, value) {
      restoreSelection();
      document.execCommand('styleWithCSS', false, command !== 'formatBlock' && command !== 'insertUnorderedList' && command !== 'insertOrderedList');
      document.execCommand(command, false, value === undefined ? null : value);
      sync();
    }
    function insertHtml(html) {
      restoreSelection();
      document.execCommand('insertHTML', false, html);
      sync();
    }

    const toolbar = document.createElement('div');
    toolbar.className = 'toolbar editor-toolbar';
    toolbar.setAttribute('aria-label', 'Formatting');

    const group = (...items) => {
      const g = document.createElement('div');
      g.className = 'editor-group';
      g.append(...items);
      toolbar.append(g);
    };

    group(
      makeButton('↶', 'Undo', () => exec('undo')),
      makeButton('↷', 'Redo', () => exec('redo')),
    );
    group(
      makeSelect('Text style', [['', 'Style'], ['p', 'Paragraph'], ['h1', 'Heading 1'], ['h2', 'Heading 2'], ['h3', 'Heading 3'], ['blockquote', 'Quote']],
        value => { if (value) exec('formatBlock', value); }),
      makeSelect('Font', [['', 'Font'], ['Arial, Helvetica, sans-serif', 'Arial'], ['Georgia, serif', 'Georgia'], ['"Times New Roman", serif', 'Times New Roman'],
        ['Verdana, sans-serif', 'Verdana'], ['"Trebuchet MS", sans-serif', 'Trebuchet'], ['"Courier New", monospace', 'Courier New']],
        value => { if (value) exec('fontName', value); }),
      makeSelect('Size', [['', 'Size'], ['13px', 'Small'], ['16px', 'Normal'], ['18px', 'Large'], ['22px', 'X-Large'], ['28px', 'Huge']],
        value => {
          if (!value) return;
          pendingSize = value;
          restoreSelection();
          document.execCommand('styleWithCSS', false, true);
          document.execCommand('fontSize', false, '7');
          sync();
        }),
    );
    group(
      makeButton('<b>B</b>', 'Bold', () => exec('bold')),
      makeButton('<i>I</i>', 'Italic', () => exec('italic')),
      makeButton('<u>U</u>', 'Underline', () => exec('underline')),
      makeButton('<s>S</s>', 'Strikethrough', () => exec('strikeThrough')),
      makeColor('Text color', '<span class="editor-color-swatch">A</span>', value => exec('foreColor', value)),
      makeColor('Highlight color', '<span class="editor-color-swatch editor-highlight">A</span>', value => exec('hiliteColor', value)),
      makeButton('T<sub>x</sub>', 'Clear formatting', () => { exec('removeFormat'); exec('unlink'); }),
    );
    group(
      makeButton('⫷', 'Align left', () => exec('justifyLeft')),
      makeButton('☰', 'Align center', () => exec('justifyCenter')),
      makeButton('⫸', 'Align right', () => exec('justifyRight')),
      makeButton('• List', 'Bullet list', () => exec('insertUnorderedList')),
      makeButton('1. List', 'Numbered list', () => exec('insertOrderedList')),
      makeButton('⇤', 'Outdent', () => exec('outdent')),
      makeButton('⇥', 'Indent', () => exec('indent')),
    );
    group(
      makeButton('Link', 'Add link', () => {
        rememberSelection();
        const value = window.prompt('Enter a link beginning with https:// or mailto:');
        if (!value || !/^(https?:\/\/|mailto:)/i.test(value)) return;
        exec('createLink', value);
      }),
      makeButton('Unlink', 'Remove link', () => exec('unlink')),
      makeButton('— Divider', 'Horizontal line', () => insertHtml('<hr style="border:0;border-top:1px solid #cbd6e2;margin:20px 0">')),
      makeButton('Button', 'Insert a button-style link', () => {
        rememberSelection();
        const text = window.prompt('Button text');
        if (!text) return;
        const href = window.prompt('Button link beginning with https://');
        if (!href || !/^https?:\/\//i.test(href)) return;
        insertHtml('<p style="margin:24px 0"><a href="' + escapeHtml(href) + '" style="display:inline-block;background-color:#1a7a7a;color:#ffffff;text-decoration:none;font-weight:bold;padding:14px 22px;border-radius:8px">' + escapeHtml(text) + '</a></p>');
      }),
    );

    // Images: upload from the computer, reuse a previous upload, or paste an https address.
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = 'image/png,image/jpeg,image/gif,image/webp';
    fileInput.hidden = true;
    function placeImage(url, alt, width) {
      const displayWidth = width && width > IMAGE_MAX_PIXEL_WIDTH ? IMAGE_MAX_PIXEL_WIDTH : width;
      insertHtml('<img src="' + escapeHtml(url) + '" alt="' + escapeHtml(alt || '') + '"' + (displayWidth ? ' width="' + displayWidth + '"' : '') + ' style="max-width:100%;height:auto;display:block;margin:12px 0">');
    }
    fileInput.addEventListener('change', async () => {
      const file = fileInput.files && fileInput.files[0];
      fileInput.value = '';
      if (!file) return;
      status.textContent = 'Uploading ' + file.name + '…';
      const body = new FormData();
      body.append('image', file);
      try {
        const response = await fetch(config.uploadUrl, { method: 'POST', body, headers: { 'X-CSRFToken': csrf }, credentials: 'same-origin' });
        const result = await response.json();
        if (!response.ok || !result.url) throw new Error(result.error || 'Upload failed.');
        placeImage(result.url, file.name.replace(/\.[^.]+$/, ''), result.width);
        library.add(result);
        status.textContent = 'Image added. To resize it, edit its width in the HTML view.';
      } catch (error) {
        status.textContent = error.message || 'Upload failed.';
      }
    });

    const library = {
      items: (config.images || []).slice(),
      add(item) { this.items.unshift(item); },
      open() {
        rememberSelection();
        const dialog = document.createElement('dialog');
        dialog.className = 'editor-dialog';
        const title = document.createElement('h3');
        title.textContent = 'Insert image';
        const uploadRow = document.createElement('p');
        const uploadButton = makeButton('Upload from computer', 'Upload an image', () => { dialog.close(); fileInput.click(); }, 'btn-primary');
        const urlButton = makeButton('Use image address', 'Insert an image by https:// address', () => {
          const value = window.prompt('Image address beginning with https://');
          if (!value || !/^https:\/\//i.test(value)) return;
          dialog.close();
          placeImage(value, '', 0);
        });
        uploadRow.append(uploadButton, ' ', urlButton);
        const grid = document.createElement('div');
        grid.className = 'editor-image-grid';
        if (!this.items.length) {
          const empty = document.createElement('p');
          empty.className = 'subtle';
          empty.textContent = 'No uploaded images yet.';
          grid.append(empty);
        }
        for (const item of this.items) {
          const tile = document.createElement('button');
          tile.type = 'button';
          tile.className = 'editor-image-tile';
          tile.title = item.name;
          const img = document.createElement('img');
          img.src = item.url;
          img.alt = item.name;
          img.loading = 'lazy';
          tile.append(img);
          tile.addEventListener('click', () => { dialog.close(); placeImage(item.url, item.name.replace(/\.[^.]+$/, ''), item.width); });
          grid.append(tile);
        }
        const close = makeButton('Cancel', 'Close', () => dialog.close());
        dialog.append(title, uploadRow, grid, close);
        dialog.addEventListener('close', () => dialog.remove());
        document.body.append(dialog);
        dialog.showModal();
      },
    };
    group(makeButton('🖼 Image', 'Insert image', () => library.open()));

    const placeholderOptions = [['', 'Insert placeholder…']].concat(Object.entries(placeholders).map(([token, label]) => [token, label + ' — {{' + token + '}}']));
    if (placeholderOptions.length > 1) {
      group(makeSelect('Insert placeholder', placeholderOptions, token => { if (token) insertHtml(escapeHtml('{{' + token + '}}')); }));
    }

    const sourceToggle = makeButton('&lt;/&gt; HTML', 'Edit HTML source', () => {
      if (source.hidden) {
        source.value = editor.innerHTML;
        source.hidden = false;
        editor.hidden = true;
        sourceToggle.classList.add('is-active');
      } else {
        editor.replaceChildren(safeFragment(source.value));
        source.hidden = true;
        editor.hidden = false;
        sourceToggle.classList.remove('is-active');
        sync();
      }
    });
    group(sourceToggle);

    editor.addEventListener('input', sync);
    editor.addEventListener('keyup', rememberSelection);
    editor.addEventListener('mouseup', rememberSelection);
    editor.addEventListener('blur', rememberSelection);
    source.addEventListener('input', () => { textarea.value = source.value; editor.replaceChildren(safeFragment(source.value)); editor.dispatchEvent(new CustomEvent('automated-editor:change', { bubbles: true })); });
    editor.addEventListener('paste', event => {
      event.preventDefault();
      const html = event.clipboardData.getData('text/html');
      if (html) {
        const holder = document.createElement('div');
        holder.append(safeFragment(html));
        document.execCommand('insertHTML', false, holder.innerHTML);
      } else {
        document.execCommand('insertText', false, event.clipboardData.getData('text/plain'));
      }
      sync();
    });
    editor.addEventListener('drop', event => {
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file && file.type.startsWith('image/')) {
        event.preventDefault();
        const transfer = new DataTransfer();
        transfer.items.add(file);
        fileInput.files = transfer.files;
        fileInput.dispatchEvent(new Event('change'));
      }
    });
    textarea.form.addEventListener('submit', () => { if (!source.hidden) editor.replaceChildren(safeFragment(source.value)); textarea.value = editor.innerHTML; });

    textarea.hidden = true;
    textarea.required = false;
    shell.append(toolbar, editor, source, status, fileInput);
    textarea.before(shell);
    textarea.value = editor.innerHTML;
  }

  // Live preview: mirrors every field into the sample preview as the administrator types.
  const preview = document.getElementById('automated-preview');
  if (preview) {
    const form = document.querySelector('form[data-automated-form]');
    function render() {
      for (const target of preview.querySelectorAll('[data-preview-field]')) {
        const name = target.dataset.previewField;
        const field = form.elements[name];
        if (!field) continue;
        const value = field.value || '';
        const wrapper = target.closest('[data-preview-wrap]') || target;
        wrapper.hidden = !value.trim();
        if (target.dataset.previewRich === '1') {
          target.replaceChildren(safeFragment(fillSample(value)));
        } else {
          target.textContent = fillSampleText(value);
        }
      }
    }
    form.addEventListener('input', render);
    form.addEventListener('automated-editor:change', render);
    render();
  }
})();
