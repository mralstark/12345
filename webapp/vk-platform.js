/* VK-only platform adapter. Launch credentials stay in memory, never storage. */
(function () {
  'use strict';
  const bridge = window.vkBridge;
  const params = new URLSearchParams(location.search);
  const launchData = params.toString();
  let linkToken = new URLSearchParams(location.hash.replace(/^#/, '')).get('link');
  if (!/^[A-Za-z0-9_-]{43}$/.test(linkToken || '')) linkToken = null;

  function send(method, args, timeout) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('VK не ответил. Повторите действие.')), timeout || 12000);
      Promise.resolve().then(() => bridge.send(method, args || {})).then(
        (result) => { clearTimeout(timer); resolve(result); },
        () => { clearTimeout(timer); reject(new Error('Действие в VK отменено или недоступно.')); });
    });
  }

  function insets(config) {
    const value = config && config.insets;
    if (!value) return;
    for (const name of ['top', 'bottom']) {
      const amount = Number(value[name]);
      if (Number.isFinite(amount) && amount >= 0 && amount <= 160) {
        document.documentElement.style.setProperty('--vk-safe-' + name, amount + 'px');
      }
    }
  }

  window.CabinetVK = {
    launchData,
    get linkToken() { return linkToken; },
    clearLink() {
      linkToken = null;
      const hash = new URLSearchParams(location.hash.replace(/^#/, ''));
      hash.delete('link');
      history.replaceState(null, '', location.pathname + location.search + (hash.size ? '#' + hash : ''));
      send('VKWebAppSetLocation', { location: hash.toString() }, 2000).catch(() => {});
    },
    async initialize() {
      if (!bridge || typeof bridge.send !== 'function') throw new Error('Не удалось подключиться к VK. Переоткройте приложение.');
      await send('VKWebAppInit', {}, 8000);
      document.body.dataset.platform = 'vk';
      if (typeof bridge.subscribe === 'function') bridge.subscribe((event) => {
        const detail = event.detail || {};
        if (detail.type === 'VKWebAppUpdateConfig') insets(detail.data);
        if (detail.type === 'VKWebAppViewRestore') window.dispatchEvent(new Event('cabinet:restore'));
      });
      send('VKWebAppGetConfig', {}, 3000).then(insets).catch(() => {});
      const profile = await send('VKWebAppGetUserInfo', {}, 4000).catch(() => null);
      // Profile is only an editable registration suggestion; server trusts sign.
      this.fullName = profile && String(profile.id) === params.get('vk_user_id')
        ? [profile.first_name, profile.last_name].filter(Boolean).join(' ').slice(0, 128) : '';
    },
    async requestPhone() {
      const data = await send('VKWebAppGetPhoneNumber', {}, 60000);
      if (!data || !data.phone_number || !data.sign || data.is_verified === false) {
        throw new Error('VK не передал подтверждённый номер телефона.');
      }
      return { phone_number: String(data.phone_number), sign: String(data.sign) };
    },
    canDownload() { return typeof bridge.supports === 'function' && bridge.supports('VKWebAppDownloadFile'); },
    async download(file) {
      const url = new URL(file.url, location.origin);
      if (url.origin !== location.origin || !url.pathname.startsWith('/api/reports/files/')) {
        throw new Error('Некорректная ссылка скачивания');
      }
      await send('VKWebAppDownloadFile', {url:url.href, filename:file.filename}, 12000);
    },
    open(url) { return send('VKWebAppOpenLink', {url}, 8000); },
    close() { return send('VKWebAppClose', {status:'success'}, 3000); },
  };
})();
