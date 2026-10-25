/* Ordinary browser entry must not wait for an unavailable Telegram domain. */
(function () {
  'use strict';
  const script = document.currentScript;
  const params = new URLSearchParams(location.search);
  const launch = new URLSearchParams(location.hash.replace(/^#/, ''));
  const vk = params.get('web') !== '1' && ['vk_app_id', 'vk_user_id', 'sign'].some((name) => params.has(name));
  const embedded = !vk && params.get('web') !== '1' && (launch.has('tgWebAppData') ||
    Boolean(window.TelegramWebviewProxy || window.external && window.external.notify));
  const articles = script && script.dataset.articles || '/static/articles.js';
  const app = script && script.dataset.app || '/static/app.js';

  function load(source, timeout) {
    return new Promise((resolve, reject) => {
      const el = document.createElement('script');
      let settled = false;
      const done = (error) => {
        if (settled) return; settled = true; clearTimeout(timer);
        el.onload = el.onerror = null;
        if (error) { el.remove(); reject(error); } else resolve();
      };
      const timer = setTimeout(() => done(new Error('timeout')), timeout);
      el.onload = () => done(); el.onerror = () => done(new Error('network'));
      el.src = source; document.head.appendChild(el);
    });
  }

  (async () => {
    if (vk) {
      await load(script.dataset.vkBridge || '/static/vendor/vk-bridge.min.js', 15000);
      await load(script.dataset.vk || '/static/vk-platform.js', 15000);
      await window.CabinetVK.initialize();
    }
    if (embedded) await load('https://telegram.org/js/telegram-web-app.js', 8000);
    await load(articles, 15000);
    await load(app, 15000);
  })().catch(() => {
    const view = document.getElementById('view');
    if (!view) return;
    view.replaceChildren();
    const card = document.createElement('div'); card.className = 'card browser-login';
    const text = document.createElement('p');
    text.textContent = 'Не удалось загрузить кабинет. Проверьте соединение и повторите попытку.';
    const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'btn';
    retry.textContent = 'Попробовать снова'; retry.onclick = () => location.reload();
    const web = document.createElement('a'); web.className = 'btn btn--ghost';
    const url = new URL(location.href); url.hash = ''; url.searchParams.set('web','1');
    for (const key of [...url.searchParams.keys()]) if (key.startsWith('vk_') || key === 'sign') url.searchParams.delete(key);
    web.href = url.href; web.textContent = 'Открыть веб-вход';
    card.append(text,retry,web); view.appendChild(card);
    const intro = document.getElementById('intro'); if (intro) intro.classList.add('intro--hidden');
  });
})();
