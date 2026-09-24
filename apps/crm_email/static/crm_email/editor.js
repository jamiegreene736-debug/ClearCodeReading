(() => {
  const tags = new Set(['P', 'DIV', 'BR', 'STRONG', 'B', 'EM', 'I', 'U', 'UL', 'OL', 'LI', 'BLOCKQUOTE', 'A']);
  function safeFragment(value) {
    const parsed = new DOMParser().parseFromString(value, 'text/html');
    function copy(node) {
      if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent);
      if (node.nodeType !== Node.ELEMENT_NODE) return document.createDocumentFragment();
      if (['SCRIPT', 'STYLE', 'IMG', 'IFRAME', 'OBJECT', 'SVG', 'MATH'].includes(node.tagName)) return document.createDocumentFragment();
      const result = tags.has(node.tagName) ? document.createElement(node.tagName.toLowerCase()) : document.createDocumentFragment();
      if (node.tagName === 'A') {
        const href = node.getAttribute('href') || '';
        if (/^(https?:\/\/|mailto:)/i.test(href)) result.setAttribute('href', href);
      }
      for (const child of node.childNodes) result.append(copy(child));
      return result;
    }
    const fragment = document.createDocumentFragment();
    for (const node of parsed.body.childNodes) fragment.append(copy(node));
    return fragment;
  }
  for (const textarea of document.querySelectorAll('textarea[name="body_html"]:not([data-rich-editor]), textarea[name="signature"]')) {
    const editor = document.createElement('div');
    editor.className = 'rich-editor';
    editor.id = textarea.id + '_editor';
    editor.contentEditable = 'true';
    editor.setAttribute('role', 'textbox');
    editor.setAttribute('aria-multiline', 'true');
    editor.setAttribute('aria-label', textarea.name === 'signature' ? 'Signature' : 'Message');
    editor.append(safeFragment(textarea.value));
    const toolbar = document.createElement('div');
    toolbar.className = 'toolbar';
    toolbar.setAttribute('aria-label', 'Text formatting');
    for (const [label, command] of [['Bold', 'bold'], ['Italic', 'italic'], ['Bullet list', 'insertUnorderedList'], ['Add link', 'createLink']]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn-small';
      button.textContent = label;
      button.addEventListener('mousedown', event => event.preventDefault());
      button.addEventListener('click', () => {
        let value = null;
        if (command === 'createLink') {
          value = window.prompt('Enter a link beginning with https://');
          if (!value || !/^https?:\/\//i.test(value)) return;
        }
        editor.focus();
        document.execCommand(command, false, value);
        textarea.value = editor.innerHTML;
      });
      toolbar.append(button);
    }
    editor.addEventListener('input', () => { textarea.value = editor.innerHTML; });
    editor.addEventListener('paste', event => {
      event.preventDefault();
      document.execCommand('insertText', false, event.clipboardData.getData('text/plain'));
    });
    textarea.form.addEventListener('submit', () => { textarea.value = editor.innerHTML; });
    textarea.hidden = true;
    textarea.required = false;
    textarea.before(toolbar, editor);
  }
})();
