/* Telegraph-style reading and an author workspace, without external editors. */
window.CabinetArticles = function (deps) {
  'use strict';
  const {api, esc, toast, modal, closeModal, on, photoViewer} = deps;
  const tags = new Set(['p','h3','h4','blockquote','ul','ol','li','b','strong','i','em','a','br','hr','figure','figcaption','img']);
  let active = null;

  function safeLink(url) {
    if (!url || /[\s\\]/.test(url)) return false;
    try { const u = new URL(url); return ['http:','https:'].includes(u.protocol) && !u.username && !u.password; }
    catch (_) { return false; }
  }

  function documentHtml(nodes, photos, editing) {
    const byId = new Map((photos || []).map((p) => [p.id, p]));
    function render(node, depth) {
      if (depth > 12) return '';
      if (typeof node === 'string') return esc(node);
      if (!node || !tags.has(node.tag)) return '';
      const tag = node.tag;
      const attrs = node.attrs || {};
      const children = (node.children || []).map((n) => render(n, depth + 1)).join('');
      if (tag === 'img') {
        const photo = byId.get(attrs.photo_id);
        return photo ? '<img src="' + esc(photo.url) + '" data-article-photo="' + photo.id + '" alt="Иллюстрация к статье" loading="lazy"' + (editing ? ' contenteditable="false"' : '') + '>' : '';
      }
      if (tag === 'a') return safeLink(attrs.href) ? '<a href="' + esc(attrs.href) + '" target="_blank" rel="noopener noreferrer">' + children + '</a>' : children;
      if (tag === 'br' || tag === 'hr') return '<' + tag + '>';
      return '<' + tag + (editing && tag === 'figure' ? ' contenteditable="false"' : '') + (editing && tag === 'figcaption' ? ' contenteditable="true" tabindex="0" role="textbox" aria-label="Подпись к фотографии" data-placeholder="Подпись к фотографии"' : '') + '>' + children + (editing && tag === 'figure' ? '<button type="button" class="article-remove-photo" aria-label="Удалить изображение">Удалить изображение</button>' : '') + '</' + tag + '>';
    }
    return (nodes || []).map((n) => render(n, 0)).join('');
  }

  function nodesFromDom(root, pasting) {
    let count = 0;
    function visit(node, depth) {
      if (++count > 2500 || depth > 12) throw new Error('Слишком сложное форматирование. Вставьте текст без оформления');
      if (node.nodeType === 3) return node.textContent;
      if (node.nodeType !== 1) return null;
      let tag = node.tagName.toLowerCase();
      if (['script','style','iframe','object','svg','video','audio','button','input'].includes(tag)) return null;
      if (tag === 'div') tag = 'p';
      if (['h1','h2'].includes(tag)) tag = 'h3';
      if (tag === 'img') {
        const id = Number(node.dataset.articlePhoto);
        return !pasting && Number.isSafeInteger(id) && id > 0 ? {tag:'img', attrs:{photo_id:id}} : null;
      }
      const children = Array.from(node.childNodes).map((n) => visit(n, depth + 1)).flat().filter((n) => n !== null);
      if (!tags.has(tag)) return children;
      if (tag === 'a') return safeLink(node.getAttribute('href')) ? {tag, attrs:{href:node.getAttribute('href')}, children} : children;
      return {tag, ...(children.length ? {children} : {})};
    }
    return Array.from(root.childNodes).map((n) => visit(n, 0)).flat().filter((n) => n !== null);
  }

  function mount() {
    if (active) active.close();
    const previousFocus = document.activeElement;
    const root = document.createElement('section');
    root.className = 'article-overlay';
    root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true');
    root.setAttribute('aria-label', 'Статья'); root.tabIndex = -1;
    document.body.appendChild(root); document.body.classList.add('article-open'); document.documentElement.classList.add('article-open');
    const viewport = window.visualViewport;
    function resizeViewport() {
      if (!viewport) return;
      root.style.height = viewport.height + 'px'; root.style.top = viewport.offsetTop + 'px'; root.style.bottom = 'auto';
    }
    if (viewport) { viewport.addEventListener('resize', resizeViewport); viewport.addEventListener('scroll', resizeViewport); resizeViewport(); }
    const bg = [document.querySelector('.topbar'),document.getElementById('tabs'),document.getElementById('view')].filter(Boolean);
    const oldInert = bg.map((el) => el.inert);
    bg.forEach((el) => { el.inert = true; });
    const tg = window.Telegram && window.Telegram.WebApp;
    let back = () => close();
    const hadBack = tg && tg.BackButton && tg.BackButton.isVisible;
    const closingConfirmation = tg && tg.isClosingConfirmationEnabled;
    function onBack() { back(); }
    function key(event) {
      if (document.querySelector('.modal-backdrop, .viewer')) return;
      if (event.key === 'Escape' && !document.querySelector('.modal-backdrop, .viewer')) { event.preventDefault(); back(); }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(root.querySelectorAll('button:not(:disabled),input:not([hidden]),textarea,select,a[href],[contenteditable="true"]')).filter((n) => n.offsetParent !== null && !n.closest('[inert]'));
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === root)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    function close() {
      root.remove(); document.body.classList.remove('article-open'); document.documentElement.classList.remove('article-open'); document.removeEventListener('keydown', key);
      if (viewport) { viewport.removeEventListener('resize', resizeViewport); viewport.removeEventListener('scroll', resizeViewport); }
      bg.forEach((el, i) => { el.inert = oldInert[i]; });
      if (tg && tg.BackButton) { tg.BackButton.offClick(onBack); if (!hadBack) tg.BackButton.hide(); }
      if (tg && !closingConfirmation && tg.disableClosingConfirmation) tg.disableClosingConfirmation();
      if (previousFocus && previousFocus.isConnected) previousFocus.focus({preventScroll:true});
      active = null;
    }
    document.addEventListener('keydown', key);
    if (tg && tg.BackButton) { tg.BackButton.onClick(onBack); tg.BackButton.show(); }
    active = {root, close, setBack(fn) { back = fn; }};
    root.focus({preventScroll:true});
    return active;
  }

  const backButton = (label, id) => '<button class="back-link" id="' + id + '" type="button"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="m15 5-7 7 7 7"/></svg>' + esc(label) + '</button>';
  function readingHtml(article) {
    return '<article class="article-paper"><div class="article-kicker">Статья</div>' +
      '<h1 class="article-title">' + esc(article.title) + '</h1>' +
      '<div class="article-meta">' + esc(article.byline || '') + '<span>·</span>' +
      esc(new Date(article.created_at || Date.now()).toLocaleDateString('ru-RU', {day:'numeric',month:'long',year:'numeric'})) +
      '<span>·</span>' + (article.reading_minutes || 1) + ' мин чтения</div>' +
      (article.summary ? '<p class="article-lead">' + esc(article.summary) + '</p>' : '') +
      '<div class="article-content">' + documentHtml(article.content, article.photos, false) + '</div>' +
      '<div class="article-end" aria-hidden="true">◆</div></article>';
  }

  function wirePhotos(root, article) {
    root.querySelectorAll('[data-article-photo]').forEach((img) => {
      img.onclick = () => {
        const id = Number(img.dataset.articlePhoto);
        photoViewer(article.photos, Math.max(0, article.photos.findIndex((p) => p.id === id)));
      };
    });
  }

  async function open(id) {
    if (active) return;
    const pane = mount();
    pane.root.innerHTML = '<div class="article-topbar">' + backButton('К новостям', 'articleBack') + '</div><div class="loader">Открываем статью…</div>';
    pane.root.querySelector('#articleBack').onclick = pane.close;
    try {
      const article = await api('/news/articles/' + id);
      if (!pane.root.isConnected) return;
      pane.root.innerHTML = '<div class="article-topbar">' + backButton('К новостям', 'articleBack') +
        (article.can_edit && deps.canEdit() ? '<button class="btn btn--small btn--ghost" id="articleEdit">Редактировать</button>' : '') +
        '</div><div class="article-scroll">' + readingHtml(article) + '<div class="article-bottom">' + backButton('К новостям', 'articleBottomBack') + '<button type="button" class="btn btn--small btn--ghost" id="articleCopyLink">Скопировать ссылку</button></div></div>';
      pane.root.querySelector('#articleBack').onclick = pane.close;
      pane.root.querySelector('#articleBottomBack').onclick = pane.close;
      pane.root.querySelector('#articleCopyLink').onclick = async () => {
        const url = new URL('/?article=' + article.id, location.origin).href;
        try { await navigator.clipboard.writeText(url); toast('Ссылка скопирована'); }
        catch (_) {
          modal('Ссылка на статью', '<div class="field"><label for="articlePermalink">Скопируйте адрес</label><input readonly id="articlePermalink" value="' + esc(url) + '"></div>', () => document.getElementById('articlePermalink').select());
        }
      };
      const edit = pane.root.querySelector('#articleEdit');
      if (edit) edit.onclick = async () => { edit.disabled = true; try {
        const draft = await api('/news/articles/' + id + '/edit', {method:'POST'});
        pane.close(); await editor(draft);
      } catch (e) { toast(e.message); edit.disabled = false; } };
      wirePhotos(pane.root, article);
      api('/news/views', {method:'POST', body:{post_ids:[article.id]}}).catch(() => {});
    } catch (e) { toast(e.message); pane.close(); }
  }

  async function drafts() {
    const data = await api('/news/articles/drafts');
    modal('Черновики статей', data.items.length ? '<div class="article-drafts">' + data.items.map((a) =>
      '<div class="row"><button type="button" class="row__main article-draft-open" data-draft="' + a.id + '">' + esc(a.title) + '</button>' +
      '<button type="button" class="btn btn--small btn--ghost" data-drop-draft="' + a.id + '" aria-label="Удалить черновик">Удалить</button></div>').join('') + '</div>' : '<p>Сохранённые черновики появятся здесь.</p>', () => {
      on('[data-draft]', 'click', async (e) => {
        const a = await api('/news/articles/' + e.currentTarget.dataset.draft); closeModal(); await editor(a);
      });
      on('[data-drop-draft]', 'click', async (e) => {
        const id = e.currentTarget.dataset.dropDraft;
        // Deleting an unpublished draft never deletes its published source.
        await api('/news/' + id, {method:'DELETE'}); await drafts();
      });
    });
  }

  async function create() {
    const identities = await deps.identities();
    const first = identities[0];
    const a = await api('/news/articles', {method:'POST', body:{official:first.official, region_id:first.regionId || null}});
    await editor(a, identities);
  }

  async function editor(initial, suppliedIdentities) {
    const identities = suppliedIdentities || await deps.identities();
    let article = initial;
    const mediaLimits = article.media_limits || {max_bytes:20 * 1024 * 1024, max_photos:10};
    const pane = mount();
    let changed = 0, saved = 0, saving = null, timer = null, actionBusy = false, range = null, previewing = false, publicationAttempt = null;
    const tg = window.Telegram && window.Telegram.WebApp;
    if (tg && tg.enableClosingConfirmation) tg.enableClosingConfirmation();
    const identityIndex = identities.findIndex((i) => article.region_id ? i.regionId === article.region_id : !i.regionId && i.kind === article.byline_kind);
    pane.root.innerHTML = '<div class="article-topbar">' + backButton('К новостям', 'articleEditorBack') +
      '<span class="article-save-state" id="articleSaveState" role="status">Черновик сохранён</span></div>' +
      '<div class="article-scroll article-editor-scroll"><div class="article-paper article-editor">' +
      '<div class="article-kicker">' + (article.source_id ? 'Редактирование статьи' : 'Новая статья') + '</div>' +
      '<label class="sr-only" for="articleTitle">Заголовок статьи</label><textarea class="article-title-input" id="articleTitle" maxlength="256" rows="2" placeholder="Заголовок статьи">' + esc(article.title) + '</textarea>' +
      '<div class="article-editor-meta"><label for="articleIdentity">От лица</label><select id="articleIdentity"' + (article.source_id ? ' disabled' : '') + '>' + identities.map((i,n) => '<option value="' + n + '"' + (n === Math.max(0,identityIndex) ? ' selected' : '') + '>' + esc(i.label) + '</option>').join('') + '</select></div>' +
      '<label class="sr-only" for="articleSummary">Краткое описание</label><textarea class="article-summary-input" id="articleSummary" maxlength="500" rows="2" placeholder="Короткое описание для ленты (необязательно)">' + esc(article.summary) + '</textarea>' +
      '<div class="article-toolbar" role="toolbar" aria-label="Форматирование статьи">' +
      [['bold','Ж','Жирный'],['italic','К','Курсив'],['h3','H1','Подзаголовок'],['h4','H2','Малый подзаголовок'],['p','Текст','Обычный текст'],['blockquote','Цитата','Цитата'],['insertUnorderedList','• Список','Маркированный список'],['insertOrderedList','1. Список','Нумерованный список'],['link','Ссылка','Добавить ссылку'],['photo','Фото','Вставить фотографию'],['removeFormat','Очистить','Убрать выделение']].map((b) => '<button type="button" class="article-tool" data-format="' + b[0] + '" title="' + b[2] + '" aria-label="' + b[2] + '">' + b[1] + '</button>').join('') + '</div>' +
      '<div id="articleBody" class="article-content article-editable" contenteditable="true" role="textbox" aria-label="Текст статьи" aria-multiline="true" data-placeholder="Начните историю…">' + documentHtml(article.content, article.photos, true) + '</div>' +
      '<input id="articleFile" type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple hidden>' +
      '<p class="article-media-hint">JPEG, PNG, GIF и WebP · до ' + Math.floor(mediaLimits.max_bytes / (1024 * 1024)) + ' МБ на файл. Можно выбрать несколько, вставить из буфера или перетащить. GIF сохраняет анимацию.</p>' +
      '<div class="article-editor-footer"><span class="row__sub">До 60 000 знаков · до ' + mediaLimits.max_photos + ' изображений</span><button class="btn btn--ghost" type="button" id="articleSave">Сохранить черновик</button><button class="btn" type="button" id="articlePreview">Предпросмотр</button></div>' +
      '</div></div><div id="articlePreviewPane" class="article-preview-pane" hidden></div>';
    const body = pane.root.querySelector('#articleBody');
    const title = pane.root.querySelector('#articleTitle');
    const summary = pane.root.querySelector('#articleSummary');
    const status = pane.root.querySelector('#articleSaveState');
    const identitySelect = pane.root.querySelector('#articleIdentity');
    const picker = pane.root.querySelector('#articleFile');
    const preview = pane.root.querySelector('#articlePreviewPane');
    const setStatus = (text, error) => { status.textContent = text; status.classList.toggle('article-save-state--error', Boolean(error)); };
    function dirty() {
      changed++; setStatus('Есть изменения'); clearTimeout(timer);
      timer = setTimeout(() => save().catch(() => {}), 1200);
    }
    function remember() {
      const selection = window.getSelection();
      if (selection && selection.rangeCount && body.contains(selection.anchorNode)) range = selection.getRangeAt(0).cloneRange();
    }
    function restore() {
      body.focus({preventScroll:true});
      const selection = window.getSelection();
      if (range && body.contains(range.commonAncestorContainer)) { selection.removeAllRanges(); selection.addRange(range); }
    }
    function insert(html) {
      restore();
      const selection = window.getSelection();
      let r = selection.rangeCount && body.contains(selection.anchorNode) ? selection.getRangeAt(0) : null;
      if (!r) { r = document.createRange(); r.selectNodeContents(body); r.collapse(false); }
      r.deleteContents();
      const fragment = r.createContextualFragment(html);
      const last = fragment.lastChild; r.insertNode(fragment);
      if (last) { r.setStartAfter(last); r.collapse(true); selection.removeAllRanges(); selection.addRange(r); }
      remember(); dirty();
    }
    function save() {
      clearTimeout(timer);
      if (saving) return saving;
      saving = (async () => {
        while (saved !== changed && pane.root.isConnected) {
          const version = changed;
          const i = identities[Number(identitySelect.value)] || identities[0];
          const payload = {title:title.value, summary:summary.value, content:nodesFromDom(body), revision:article.revision,
                           official:i.official, region_id:i.regionId || null};
          setStatus('Сохраняем…');
          article = await api('/news/articles/' + article.id, {method:'PUT', body:payload});
          saved = version; setStatus('Черновик сохранён');
        }
      })().catch((e) => { setStatus(e.status === 409 ? 'Конфликт версий — текст остаётся здесь' : 'Не сохранено — нажмите «Сохранить черновик»', true); throw e; }).finally(() => { saving = null; });
      return saving;
    }
    async function leave() {
      if (actionBusy) return;
      try { await save(); finishLeaving(); }
      catch (e) {
        const copy = title.value + '\n\n' + summary.value + '\n\n' + body.innerText;
        modal('Текст ещё не сохранён', '<p>' + esc(e.message) + '</p><p class="row__sub">Можно продолжить редактирование или скопировать свой текст перед выходом. На сервере останется последняя сохранённая версия.</p><div class="field"><label for="articleRecovery">Ваш текст для копирования</label><textarea id="articleRecovery" readonly rows="6">' + esc(copy) + '</textarea></div><div class="btn-row"><button class="btn" id="articleKeepEditing">Продолжить редактирование</button><button class="btn btn--ghost" id="articleDiscardChanges">Закрыть без сохранения</button></div>', () => {
          document.getElementById('articleKeepEditing').onclick = closeModal;
          document.getElementById('articleDiscardChanges').onclick = () => { closeModal(); finishLeaving(); };
        });
      }
    }
    function finishLeaving() {
      clearTimeout(timer); window.removeEventListener('online', reconnect); window.removeEventListener('beforeunload', unloading); pane.close();
    }
    function reconnect() { if (pane.root.isConnected && saved !== changed) save().catch(() => {}); }
    function unloading(e) { if (saved !== changed) { e.preventDefault(); e.returnValue = ''; } }
    window.addEventListener('online', reconnect); window.addEventListener('beforeunload', unloading);
    pane.setBack(() => { if (previewing) closePreview(); else leave(); });
    pane.root.querySelector('#articleEditorBack').onclick = leave;
    [title,summary,body].forEach((el) => el.addEventListener('input', dirty));
    identitySelect.onchange = dirty;
    body.addEventListener('keyup', remember); body.addEventListener('mouseup', remember); body.addEventListener('touchend', remember);
    body.addEventListener('paste', (e) => {
      e.preventDefault(); remember();
      const files = Array.from(e.clipboardData.files || []);
      if (files.length) { uploadFiles(files); return; }
      try {
        const html = e.clipboardData.getData('text/html');
        if (html) {
          const template = document.createElement('template');
          template.innerHTML = html;
          insert(documentHtml(nodesFromDom(template.content, true), [], true));
        } else insert(e.clipboardData.getData('text/plain').split(/\r?\n/).map((p) => '<p>' + esc(p) + '</p>').join(''));
      } catch (error) { toast(error.message); }
    });
    body.addEventListener('dragover', (e) => e.preventDefault());
    body.addEventListener('drop', (e) => {
      e.preventDefault(); if (!actionBusy) { remember(); uploadFiles(Array.from(e.dataTransfer.files || [])); }
    });
    body.addEventListener('click', (e) => {
      const remove = e.target.closest('.article-remove-photo');
      if (remove && !actionBusy) { remove.closest('figure').remove(); range = null; dirty(); }
    });
    const buttons = pane.root.querySelectorAll('[data-format]');
    buttons.forEach((button) => {
      button.addEventListener('mousedown', (e) => { e.preventDefault(); remember(); });
      button.onclick = () => {
        if (actionBusy) return;
        const command = button.dataset.format;
        if (command === 'photo') { remember(); picker.click(); return; }
        if (command === 'link') {
          remember(); modal('Добавить ссылку', '<div class="field"><label for="articleLink">Адрес ссылки</label><input id="articleLink" type="url" placeholder="https://" autocomplete="off"></div><div class="btn-row"><button class="btn" id="articleLinkSave">Добавить</button><button class="btn btn--ghost" id="articleLinkCancel">Отмена</button></div>', () => {
            document.getElementById('articleLinkCancel').onclick = closeModal;
            document.getElementById('articleLinkSave').onclick = () => {
              const href = document.getElementById('articleLink').value.trim();
              if (!safeLink(href)) { toast('Укажите ссылку с https:// или http://'); return; }
              closeModal(); restore();
              const text = window.getSelection().toString() || href;
              insert('<a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + esc(text) + '</a>');
            };
          }); return;
        }
        restore();
        if (['p','h3','h4','blockquote'].includes(command)) document.execCommand('formatBlock', false, command);
        else document.execCommand(command, false);
        remember(); dirty();
      };
    });
    function lockEditor(locked) {
      body.contentEditable = String(!locked); title.disabled = summary.disabled = locked;
      identitySelect.disabled = locked || Boolean(article.source_id);
      pane.root.querySelectorAll('.article-toolbar button, #articleSave, #articlePreview, .article-remove-photo').forEach((b) => { b.disabled = locked; });
      body.querySelectorAll('figcaption').forEach((c) => { c.contentEditable = String(!locked); });
    }
    async function uploadFiles(files) {
      if (!files.length || actionBusy || previewing || !pane.root.isConnected) return;
      const count = new Set(Array.from(body.querySelectorAll('[data-article-photo]'), (p) => p.dataset.articlePhoto)).size;
      if (count + files.length > mediaLimits.max_photos) { toast('В статье может быть не больше ' + mediaLimits.max_photos + ' изображений'); return; }
      if (files.some((f) => !f.size || f.size > mediaLimits.max_bytes)) { toast('Выберите непустые изображения не больше ' + Math.floor(mediaLimits.max_bytes / (1024 * 1024)) + ' МБ каждое'); return; }
      actionBusy = true;
      let uploaded = 0;
      try {
        lockEditor(true); await save();
        for (const file of files) {
          setStatus('Загружаем изображение ' + (uploaded + 1) + ' из ' + files.length + '…');
          const previous = new Set(article.photos.map((p) => p.id));
          const form = new FormData(); form.append('revision', article.revision); form.append('file', file);
          const controller = new AbortController();
          const timeout = setTimeout(() => controller.abort(), 120000);
          const uploadKey = window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : 'upload-' + Date.now() + '-' + Math.random().toString(36).slice(2);
          try {
            // Same key on the immediate network retry: never duplicate a file
            // whose successful response was lost on a mobile connection.
            const send = () => api('/news/articles/' + article.id + '/photos', {method:'POST', headers:{'Idempotency-Key':uploadKey}, body:form, signal:controller.signal});
            try { article = await send(); }
            catch (error) {
              if (error.name !== 'AbortError' && !error.status && !controller.signal.aborted) {
                await new Promise((resolve) => setTimeout(resolve, 1600)); article = await send();
              } else throw error;
            }
          } finally { clearTimeout(timeout); }
          const photo = article.photos.find((p) => !previous.has(p.id));
          if (!photo) throw new Error('Не удалось получить изображение. Откройте сохранённый черновик');
          uploaded++;
          // The server has already appended the image atomically. Move it to
          // the author's cursor in the next save; its fallback is still safe.
          body.contentEditable = 'true';
          insert(documentHtml([{tag:'figure',children:[{tag:'img',attrs:{photo_id:photo.id}},{tag:'figcaption',children:[]}]}], [photo], true) + '<p><br></p>');
          body.contentEditable = 'false'; await save();
        }
        setStatus('Черновик сохранён');
      } catch (e) {
        setStatus(uploaded ? 'Изображения добавлены: ' + uploaded + '. Проверьте сохранение черновика' : 'Загрузка не завершена — текст сохранён', true);
        toast(e.name === 'AbortError' ? 'Загрузка заняла слишком много времени. Проверьте связь и откройте сохранённый черновик перед повторной загрузкой.' : e.message);
      } finally { lockEditor(false); actionBusy = false; }
    }
    picker.onchange = () => {
      const files = Array.from(picker.files); picker.value = ''; uploadFiles(files);
    };
    async function previewArticle() {
      if (actionBusy) return; actionBusy = true;
      try {
        await save();
        if (!article.title.trim() || !body.textContent.trim()) { toast('Добавьте заголовок и текст статьи'); return; }
        previewing = true; preview.hidden = false;
        pane.root.querySelector('.article-editor-scroll').inert = true;
        pane.root.querySelector('.article-topbar').inert = true;
        preview.innerHTML = '<div class="article-topbar">' + backButton('К редактору', 'articlePreviewBack') + '<span class="article-save-state">Предпросмотр</span></div><div class="article-scroll">' + readingHtml(article) +
          '<div class="article-publish-footer"><p class="row__sub">' + (article.source_id ? 'Обновление сохранит адрес статьи. Повторного уведомления не будет.' : 'После публикации статья появится в ленте. ' + esc((identities[Number(identitySelect.value)] || {}).audience || 'Личная подпись — без уведомлений.')) + '</p><button class="btn" id="articlePublish">' + (article.source_id ? 'Сохранить изменения' : 'Опубликовать статью') + '</button></div></div>';
        preview.querySelector('#articlePreviewBack').onclick = closePreview;
        preview.querySelector('#articlePreviewBack').focus({preventScroll:true});
        wirePhotos(preview, article);
        let publishing = false;
        preview.querySelector('#articlePublish').onclick = async (e) => {
          if (publishing) return; publishing = true; actionBusy = true; e.currentTarget.disabled = true;
          try {
            if (!publicationAttempt || publicationAttempt.revision !== article.revision) publicationAttempt = {
              revision:article.revision, key:window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : 'article-' + Date.now() + '-' + Math.random().toString(36).slice(2),
            };
            await api('/news/articles/' + article.id + '/publish', {method:'POST', headers:{'Idempotency-Key':publicationAttempt.key}, body:{revision:article.revision}});
            clearTimeout(timer); window.removeEventListener('online', reconnect); window.removeEventListener('beforeunload', unloading);
            pane.close(); toast(article.source_id ? 'Статья обновлена' : 'Статья опубликована'); deps.refreshFeed();
          } catch (error) { publishing = false; actionBusy = false; toast(error.message); if (preview.isConnected) preview.querySelector('#articlePublish').disabled = false; }
        };
      } catch (e) { toast(e.message); } finally { actionBusy = false; }
    }
    function closePreview() {
      if (actionBusy) return;
      previewing = false; preview.hidden = true; preview.innerHTML = '';
      pane.root.querySelector('.article-editor-scroll').inert = false;
      pane.root.querySelector('.article-topbar').inert = false;
      restore();
    }
    pane.root.querySelector('#articlePreview').onclick = previewArticle;
    pane.root.querySelector('#articleSave').onclick = async () => { if (actionBusy) return; try { await save(); toast('Черновик сохранён'); } catch (e) { toast(e.message); } };
  }
  return {open, create, drafts, documentHtml, nodesFromDom, safeLink};
};
