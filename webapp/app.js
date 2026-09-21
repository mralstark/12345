/* Кабинет Братства Академистов — Mini App.
   Ванильный JS без сборки: страница отдаётся тем же FastAPI, что и API,
   авторизация — заголовком Authorization: tma <initData> (см. api/auth.py). */

(function () {
  'use strict';

  // Самопроверка версии: у некоторых клиентов Telegram (замечено на
  // телефонах) WebView конкретного открытого чата с ботом не перезагружается
  // заново даже после закрытия/переоткрытия и очистки кэша Telegram в
  // настройках — версия в адресе app.js и no-store на index.html тут
  // бессильны, потому что сама эта HTML-страница тоже не перезапрашивается.
  // /webapp-version всегда идёт по сети напрямую (сам no-store) — если он
  // разошёлся с версией, которую браузер использовал при загрузке ЭТОГО
  // app.js (видна в его же src), значит перед нами точно устаревшая
  // закэшированная копия, и единственный выход — жёсткий переход на
  // заведомо новый адрес, чтобы WebView не смог отдать его из кэша.
  (function checkVersion() {
    const src = document.currentScript && document.currentScript.src;
    const match = src && src.match(/[?&]v=(\d+)/);
    if (!match) return; // открыли без ?v= (локальная отладка не через index()) — не проверяем
    const myVersion = match[1];
    if (sessionStorage.getItem('bratstvo_version_reloaded') === myVersion) return; // уже перегружались на эту версию — не зацикливаемся

    fetch('/webapp-version', { cache: 'no-store' })
      .then((r) => r.json())
      .then((data) => {
        if (String(data.version) !== myVersion) {
          sessionStorage.setItem('bratstvo_version_reloaded', String(data.version));
          location.href = '/?_r=' + Date.now();
        }
      })
      .catch(() => { /* версия недоступна — работаем с тем, что загрузилось */ });
  })();

  // Широкую вёрстку (styles.css .layout-desktop) включаем по ширине окна, а
  // не по кнопке в боте: спрашивать человека, с чего он сидит, было незачем —
  // окно и так это говорит. ?layout=desktop оставлен ради старых ссылок,
  // которые уже разосланы.
  const DESKTOP_MIN_WIDTH = 700;
  const forceDesktopLayout = new URLSearchParams(location.search).get('layout') === 'desktop';

  function applyLayoutWidth() {
    // Разворот на весь экран сам меняет ширину окна, поэтому решаем не один
    // раз при загрузке, а каждый раз заново.
    document.documentElement.classList.toggle(
      'layout-desktop', forceDesktopLayout || window.innerWidth >= DESKTOP_MIN_WIDTH
    );
  }
  applyLayoutWidth();
  window.addEventListener('resize', applyLayoutWidth);

  const tg = window.Telegram && window.Telegram.WebApp;

  // Фирменная тема (тона academists.ru) — светлая временно отключена по
  // просьбе (переключатель спрятан в index.html, не удалён), всегда тёмная.
  // Код переключения оставлен рабочим специально, чтобы включить обратно
  // было достаточно вернуть исходный initTheme и убрать hidden у кнопки.
  const THEME_KEY = 'bratstvo_theme';
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = theme === 'dark' ? '🌙' : '☀️';
  }
  function initTheme() {
    applyTheme('dark');
  }
  initTheme();
  document.getElementById('themeToggle').onclick = () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  };

  // Полноэкранное приветствие (webapp/index.html #intro) — «Добрый день,
  // {Имя}» на фоне случайного кадра жизни Братства с подписью-цитатой снизу
  // (в духе Сбербанка), зовётся из boot() как только известно имя (state.me),
  // держится INTRO_HOLD_MS, потом гаснет. Кадр меняется на каждый новый вход —
  // просто случайный выбор из списка, не последовательность без повторов.
  // Список рассчитан на то, чтобы добавлять новые кадры просто новыми
  // записями — остальные категории (съезд/фронт) уберут и подберут заново.
  const INTRO_BACKGROUNDS = [
    { src: '/static/intro-bg-ball.jpg', quote: '«Кругом блеск, шум, говор, И прекрасные черты...»', author: 'Весенний Бал Академистов' },
    { src: '/static/intro-bg-hangout.jpg', quote: '«Дружба да братство дороже всякого богатства»', author: 'Имперские салоны' },
    { src: '/static/intro-bg-brat.jpg', quote: '«Единство духа в союзе мира»', author: 'Братский день' },
    { src: '/static/intro-bg-sjezd.jpg', quote: '«Сердце разумного ищет знания»', author: 'Съезд Академистов Сибири' },
  ];
  const INTRO_HOLD_MS = 2600;

  function greetingWord() {
    const hour = new Date().getHours();
    if (hour < 6) return 'Доброй ночи';
    if (hour < 12) return 'Доброе утро';
    if (hour < 18) return 'Добрый день';
    return 'Добрый вечер';
  }

  function firstName(fullName) {
    // ФИО хранится «Фамилия Имя Отчество» — как и first_and_patronymic на
    // сервере (utils/parser.py), только тут берём одно имя, без отчества.
    const parts = (fullName || '').trim().split(/\s+/);
    return parts.length >= 2 ? parts[1] : (parts[0] || '');
  }

  function showIntroGreeting(fullName) {
    const intro = document.getElementById('intro');
    const bg = document.getElementById('introBg');
    const greeting = document.getElementById('introGreetingWrap');
    const text = document.getElementById('introGreeting');
    const caption = document.getElementById('introCaptionWrap');
    const quote = document.getElementById('introQuote');
    const author = document.getElementById('introAuthor');
    if (!intro || !bg || !greeting || !text || !caption || !quote || !author) return;

    const pic = INTRO_BACKGROUNDS[Math.floor(Math.random() * INTRO_BACKGROUNDS.length)];
    bg.style.backgroundImage = "url('" + pic.src + "')";
    text.textContent = greetingWord() + ', ' + firstName(fullName);
    quote.textContent = pic.quote;
    // Подпись под цитатой не у всех кадров есть (см. INTRO_BACKGROUNDS) —
    // просто прячем строку, если для этого кадра её не задали.
    author.textContent = pic.author || '';
    author.style.display = pic.author ? '' : 'none';

    requestAnimationFrame(() => {
      bg.classList.add('intro__bg--visible');
      greeting.classList.add('intro__greeting--visible');
      caption.classList.add('intro__caption--visible');
    });
    setTimeout(() => { intro.classList.add('intro--hidden'); }, INTRO_HOLD_MS);
  }

  if (tg) {
    tg.ready();
    tg.expand();
    // expand() тянется только до высоты контента и на Telegram Desktop почти
    // не помогает — там Mini App может открыться совсем маленьким окном.
    // requestFullscreen() (Bot API 8.0+) разворачивает по-настоящему, и теперь
    // просим его везде. Раньше на телефоне его избегали: он уходит под
    // нативную шапку Telegram («Закрыть», часы, стрелка), и та перекрывала
    // нашу. Теперь шапка отступает на --top-inset (styles.css), где эта панель
    // как раз и учтена, — перекрывать нечему.
    try {
      if (
        typeof tg.requestFullscreen === 'function' &&
        (!tg.isVersionAtLeast || tg.isVersionAtLeast('8.0'))
      ) {
        tg.requestFullscreen();
      }
    } catch (error) { /* клиент не поддерживает — остаёмся в обычном режиме */ }
    // Разворот меняет ширину окна не мгновенно — пересчитываем вёрстку, когда
    // Telegram сообщит новый размер.
    if (typeof tg.onEvent === 'function') {
      tg.onEvent('viewportChanged', applyLayoutWidth);
      tg.onEvent('viewportChanged', revealFocusedField);
      tg.onEvent('fullscreenChanged', applyLayoutWidth);
    }
  }

  // Экранная клавиатура занимает нижнюю половину экрана, а «О себе» стоит в
  // конце длинной формы — набирать приходилось вслепую, не видя, что пишешь.
  // Подтягиваем поле дважды: сразу после фокуса (с задержкой на выезд
  // клавиатуры) и ещё раз, когда высота окна действительно убавилась. Один
  // источник ненадёжен: про изменение высоты разные клиенты сообщают
  // по-разному, а иногда не сообщают вовсе.
  let focusedField = null;
  const KEYBOARD_DELAY_MS = 320;

  function isTextField(node) {
    return !!node && (node.tagName === 'TEXTAREA'
      || (node.tagName === 'INPUT' && !['checkbox', 'radio', 'file', 'button'].includes(node.type)));
  }

  function revealFocusedField() {
    if (!focusedField || !focusedField.isConnected || document.activeElement !== focusedField) return;
    focusedField.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  document.addEventListener('focusin', (event) => {
    focusedField = isTextField(event.target) ? event.target : null;
    // Клавиатура выезжает не мгновенно: подтянуть поле сразу — значит
    // подтянуть его туда, где через полсекунды будет клавиатура. Ждём.
    if (focusedField) setTimeout(revealFocusedField, KEYBOARD_DELAY_MS);
  });
  document.addEventListener('focusout', () => { focusedField = null; });

  // И ещё раз — когда место под полем действительно убавилось. Клиенты
  // сообщают об этом по-разному: visualViewport есть в браузере, а Telegram
  // шлёт свой viewportChanged (подписка ниже, где создаётся tg).
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', revealFocusedField);
  }
  window.addEventListener('resize', revealFocusedField);

  const state = {
    me: null,
    regionId: null,
    tab: 'dashboard',
    financePeriod: { kind: 'month', offset: 0 },
    analyticsPeriod: { kind: 'month', offset: 0 },
    eventsScope: 'upcoming',
    calendarMonth: null,
    myEventsCalendarMonth: null,
    profileEdit: false,
    personId: null,
    personBackTab: null,
    membersFilter: { q: '', status: '' },
    tasksBox: 'inbox',
    lobbyBranch: null,
    shopTab: null,
    shopFilter: null,
  };

  // --- Инфраструктура -------------------------------------------------------

  // Ошибка проверки формата приходит от сервера не строкой, а списком
  // служебных объектов — с английским текстом, путём до поля и внутренним
  // кодом. Печатать это человеку нельзя: однажды так и вышло — в форме
  // регистрации вместо подсказки вылез сырой JSON, и человек не
  // зарегистрировался. Переводим известные случаи, остальное сводим к общей
  // фразе, а подробность оставляем в консоли — она нужна только разработчику.
  function validationMessage(detail) {
    const list = Array.isArray(detail) ? detail : [detail];
    const first = list[0] || {};
    const known = {
      missing: 'Заполнено не всё — проверьте поля формы',
      string_too_short: 'Слишком короткое значение',
      string_too_long: 'Слишком длинное значение',
      string_pattern_mismatch: 'Значение записано не в том формате',
      int_parsing: 'Здесь нужно целое число',
      float_parsing: 'Здесь нужно число',
      date_parsing: 'Дата записана не в том формате',
      greater_than_equal: 'Значение слишком мало',
      less_than_equal: 'Значение слишком велико',
    };
    if (typeof console !== 'undefined' && console.warn) console.warn('Проверка формата:', detail);
    return known[first.type] || 'Проверьте, правильно ли заполнены поля';
  }

  // Название вуза короче двух букв сервер не примет. Проверяем здесь, чтобы
  // человек увидел понятную подсказку, а не отказ по формату: именно так и
  // вышло однажды — в поле написали «-», и вместо объяснения вылезла
  // служебная ошибка.
  const UNIVERSITY_MIN_LENGTH = 2;

  function universityNameProblem(name) {
    if (name && name.length < UNIVERSITY_MIN_LENGTH) {
      // Не «нужно N символов»: счёт букв — это про то, как устроена проверка,
      // а человеку надо сказать, что от него требуется.
      return 'Напишите название вуза полностью';
    }
    return null;
  }

  async function api(path, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    opts.headers = Object.assign({}, opts.headers);
    if (tg && tg.initData) opts.headers['Authorization'] = 'tma ' + tg.initData;
    if (opts.body && !(opts.body instanceof FormData)) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    const response = await fetch('/api' + path, opts);
    if (!response.ok) {
      let detail = 'Ошибка ' + response.status;
      try {
        const data = await response.json();
        if (data && typeof data.detail === 'string') {
          detail = data.detail;
        } else if (data && data.detail) {
          detail = validationMessage(data.detail);
        }
      } catch (e) { /* тело не JSON — оставляем код ответа */ }
      throw new Error(detail);
    }
    if (response.status === 204) return null;
    return response.json();
  }

  async function download(path, fallbackName) {
    const headers = {};
    if (tg && tg.initData) headers['Authorization'] = 'tma ' + tg.initData;
    const response = await fetch('/api' + path, { headers });
    if (!response.ok) throw new Error('Не удалось сформировать файл');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = fallbackName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  function esc(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Маска телефона: всегда +7, ровно 10 цифр после неё (11 всего, как в
  // российском мобильном номере). "8" в начале молча меняем на "7" —
  // так обычно вводят номер по привычке.
  function applyPhoneMask(input) {
    if (!input) return;
    input.type = 'tel';
    input.placeholder = '+7 900 000-00-00';
    const format = () => {
      let digits = input.value.replace(/\D/g, '');
      if (digits[0] === '8') digits = '7' + digits.slice(1);
      if (digits && digits[0] !== '7') digits = '7' + digits;
      digits = digits.slice(0, 11);
      if (!digits) { input.value = ''; return; }
      let out = '+7';
      const rest = digits.slice(1);
      if (rest.length) out += ' ' + rest.slice(0, 3);
      if (rest.length > 3) out += ' ' + rest.slice(3, 6);
      if (rest.length > 6) out += '-' + rest.slice(6, 8);
      if (rest.length > 8) out += '-' + rest.slice(8, 10);
      input.value = out;
    };
    input.addEventListener('input', format);
    if (input.value) format();
  }

  // Дата текстом (ДД.ММ.ГГГГ) — не native date picker (см. план «Снизу
  // вверх», §2, форма регистрации). Без маски человек мог вписать что угодно
  // и сколько угодно цифр; маска сама расставляет точки по мере ввода и
  // ограничивает длину, тот же приём, что и applyPhoneMask выше.
  function applyDateMask(input) {
    if (!input) return;
    input.inputMode = 'numeric';
    // Своя подсказка важнее общей: «20.02.2000» уместно у даты рождения и
    // сбивает с толку у срока задачи.
    if (!input.placeholder) input.placeholder = '20.02.2000';
    const format = () => {
      const digits = input.value.replace(/\D/g, '').slice(0, 8);
      let out = digits.slice(0, 2);
      if (digits.length > 2) out += '.' + digits.slice(2, 4);
      if (digits.length > 4) out += '.' + digits.slice(4, 8);
      input.value = out;
    };
    input.addEventListener('input', format);
    if (input.value) format();
  }

  function money(kopecks) {
    if (kopecks === null || kopecks === undefined) return '—';
    const sign = kopecks < 0 ? '−' : '';
    const abs = Math.abs(kopecks);
    const rubles = Math.floor(abs / 100);
    const cents = abs % 100;
    const formatted = rubles.toLocaleString('ru-RU');
    return sign + formatted + (cents ? ',' + String(cents).padStart(2, '0') : '') + ' ₽';
  }

  function dateRu(iso) {
    if (!iso) return '—';
    const d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso);
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' });
  }

  // Дата цифрами: «31.08.2026». В узкой строке «31 авг. 2026 г.» переносится
  // так, что «г.» уезжает на следующую строку одно, — читается как обрывок.
  function dateNum(iso) {
    if (!iso) return '';
    const d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso);
    return String(d.getDate()).padStart(2, '0') + '.' +
      String(d.getMonth() + 1).padStart(2, '0') + '.' + d.getFullYear();
  }

  function dateShort(iso) {
    if (!iso) return '';
    const d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso);
    return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
  }

  // ISO (как хранит и отдаёт API) <-> ДД.ММ.ГГГГ (как вводит человек, см.
  // applyDateMask) — карточка в «Составе» использует тот же формат ручного
  // ввода, что и форма регистрации, вместо календаря (узкое поле, без
  // растягивания строки на мобильных).
  function isoToRuDate(iso) {
    if (!iso) return '';
    const [y, m, d] = iso.split('-');
    return d + '.' + m + '.' + y;
  }
  function ruDateToIso(ru) {
    const match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec((ru || '').trim());
    return match ? match[3] + '-' + match[2] + '-' + match[1] : null;
  }

  function toast(text) {
    const node = document.createElement('div');
    node.className = 'toast';
    node.textContent = text;
    document.body.appendChild(node);
    setTimeout(() => node.remove(), 2600);
  }

  function fail(error) {
    // Устаревший рендер — не поломка, а смена экрана: молчим.
    if (error === STALE_RENDER) return;
    toast(error && error.message ? error.message : 'Что-то пошло не так');
  }

  function canEdit() {
    return state.me && state.me.editable_region_ids.indexOf(state.regionId) !== -1;
  }

  function view() { return document.getElementById('view'); }

  // --- Номер рендера экрана -------------------------------------------------
  // Любой рендер асинхронный: пока он ждёт данные с сервера, человек успевает
  // нажать другую вкладку и запустить следующий. Ответ первого приходит, когда
  // на экране уже другое, — и затирает его: внизу подсвечена одна вкладка, а
  // показано содержимое другой. Номер отсекает такие ответы — писать в #view
  // вправе только самый свежий рендер.

  let viewGen = 0;

  // Метка, которой сворачивается устаревший рендер. Именно бросаем, а не
  // молча пропускаем запись: иначе рендерер пошёл бы дальше и навесил свои
  // обработчики на узлы чужого, уже показанного экрана.
  const STALE_RENDER = { stale: true };

  // Единственная дверь к рендеру экрана: поднимает номер и отдаёт его
  // рендереру, чтобы тот пометил им свою запись.
  function startRender(renderer) {
    const gen = ++viewGen;
    Promise.resolve()
      .then(() => renderer(gen))
      .catch((error) => { if (error !== STALE_RENDER && gen === viewGen) fail(error); });
  }

  // gen — номер рендера, от имени которого идёт запись. Без него (вызов не из
  // рендерера — например, фатальная ошибка при запуске) пишем всегда.
  // Между разделами — короткое проявление. Только при смене раздела: та же
  // анимация на каждой перерисовке (отметил задачу, поставил лайк) мигала бы
  // всем экраном на любое действие. 140 мс выбраны так, чтобы переход
  // читался, но не заставлял ждать: на кнопку жмут подряд.
  let lastRenderedTab = null;

  function setView(html, gen) {
    if (gen !== undefined && gen !== viewGen) throw STALE_RENDER;
    const node = view();
    const changed = lastRenderedTab !== state.tab;
    const from = tabDirection(lastRenderedTab, state.tab);
    lastRenderedTab = state.tab;
    node.innerHTML = html;
    if (!changed) return;
    // Перезапуск анимации: без снятия класса и обращения к offsetWidth
    // повторное добавление того же класса браузер не считает изменением.
    node.classList.remove('view--enter', 'view--from-right', 'view--from-left');
    void node.offsetWidth;
    node.classList.add('view--enter');
    if (from) node.classList.add('view--from-' + from);
  }

  // Экран приезжает с той стороны, где стоит его вкладка: ушли вправо по
  // панели — содержимое въезжает справа. Порядок берём из самой панели, а не
  // из отдельного списка, — иначе он разошёлся бы с ней при первой же правке.
  // Разделы вне панели («Новости», страница человека) направления не имеют:
  // им остаётся простое проявление.
  function tabDirection(before, after) {
    if (!before || before === after) return '';
    const ids = tabsConfig().map((item) => item.id);
    const a = ids.indexOf(before);
    const b = ids.indexOf(after);
    if (a === -1 || b === -1) return '';
    return b > a ? 'right' : 'left';
  }
  function on(selector, event, handler, root) {
    (root || document).querySelectorAll(selector).forEach((node) => node.addEventListener(event, handler));
  }

  // --- Модальные окна -------------------------------------------------------

  // Кнопка, которая срабатывает один раз за отправку. Без этого человек,
  // не увидев отклика, жмёт снова — однажды так вышло двенадцать одинаковых
  // постов за полминуты. Пока запрос идёт, кнопка выключена и говорит об этом.
  function submitOnce(button, label, handler) {
    if (!button) return;
    let busy = false;
    const original = button.textContent;
    button.onclick = async () => {
      if (busy) return;
      busy = true;
      button.disabled = true;
      button.textContent = label;
      try {
        await handler();
      } finally {
        // Модалка могла уже закрыться — тогда возвращать нечего.
        if (button.isConnected) {
          busy = false;
          button.disabled = false;
          button.textContent = original;
        }
      }
    };
  }

  // Что нужно сделать при закрытии окна — каким бы способом его ни закрыли.
  // Кнопке «Отмена» можно навесить обработчик, а крестику и щелчку по фону
  // нельзя: их рисует само окно. Форма новости держит ссылки на выбранные
  // фотографии, и отпускать их надо в любом случае.
  let modalCleanups = [];

  function onModalClose(handler) {
    modalCleanups.push(handler);
  }

  function modal(title, bodyHtml, onMount) {
    closeModal();
    const root = document.getElementById('modalRoot');
    root.innerHTML =
      '<div class="modal-backdrop"><div class="modal">' +
      '<div class="modal__header"><div class="modal__title">' + esc(title) + '</div>' +
      '<button type="button" class="modal__close" id="modalCloseX" aria-label="Закрыть">×</button></div>' +
      bodyHtml +
      '</div></div>';
    const backdrop = root.querySelector('.modal-backdrop');
    backdrop.addEventListener('click', (event) => { if (event.target === backdrop) closeModal(); });
    document.getElementById('modalCloseX').onclick = closeModal;
    if (onMount) onMount(root);
  }

  function closeModal() {
    const pending = modalCleanups;
    modalCleanups = [];
    for (const handler of pending) handler();
    document.getElementById('modalRoot').innerHTML = '';
  }

  // Строкой — как раньше, объектом — когда одной фразы мало: у снятия с
  // должности последствия шире вопроса, и о них надо сказать до нажатия.
  function confirmAction(ask, onYes) {
    const o = typeof ask === 'string' ? { body: ask } : ask;
    modal(o.title || 'Подтверждение',
      '<p>' + esc(o.body) + '</p>' +
      (o.note ? '<div class="row__sub">' + esc(o.note) + '</div>' : '') +
      '<div class="btn-row">' +
      '<button class="btn btn--danger" id="confirmYes">' + esc(o.confirmLabel || 'Да') + '</button>' +
      '<button class="btn btn--ghost" id="confirmNo">Отмена</button></div>',
      () => {
        document.getElementById('confirmYes').onclick = () => { closeModal(); onYes(); };
        document.getElementById('confirmNo').onclick = closeModal;
      });
  }

  // --- Навигация ------------------------------------------------------------

  // Federal/coordinator/superuser видят организацию не через одну общую
  // «управленческую» вкладку, а через три раздельных кабинета (иначе
  // организационные инструменты — аналитика, новости, регионы — мешаются
  // с данными одного конкретного региона в одном и том же списке вкладок):
  //   personal — «Личная информация»/«Новости», как и у всех;
  //   own      — их собственный кабинет: аналитика по всем регионам,
  //              новости, управление регионами, свои задачи —
  //              ничего из этого не привязано к одному региону;
  //   region   — конкретный регион (выбранный в селекторе) в режиме
  //              просмотра: сводка/состав/финансы/... того региона.
  // У руководителя региона и руководителя ячейки такого разделения нет —
  // у них всего один регион и он же их единственный кабинет, поведение не
  // меняется (просто государство `region` для них — единственное).
  function hasOwnCabinet() {
    return Boolean(state.me) && ['federal', 'coordinator', 'superuser'].indexOf(state.me.role) !== -1;
  }

  function tabsConfig() {
    if (state.cabinetMode === 'personal') {
      // У участника (role=participant) управленческого кабинета вовсе нет,
      // переключаться не на что.
      // Пять мест в нижней панели — ровно столько, сколько разделов. Новости
      // уехали с панели на «Главную» карточкой и в «Профиль» строкой: их
      // листают изредка, а место в панели одно из пяти.
      // Вся работа человека — в «Задачах»: и то, что поручили напрямую, и
      // задачи мероприятий. Вкладка есть у каждого, потому что получить
      // задачу может каждый; раздаёт их только руководитель, из кабинета
      // управления.
      // Панель при этом остаётся из пяти кнопок: «Академия» и «Магазин» уходят
      // под «Ещё» — за ними ходят реже, чем за своими задачами, и лишние
      // значки внизу мешают больше, чем лишнее нажатие.
      return [
        { id: 'home', label: 'Главная' },
        { id: 'myEvents', label: 'Мероприятия' },
        { id: 'character', label: 'Академия' },
        { id: 'tasks', label: 'Задачи', badge: (state.me && state.me.counters || {}).new_tasks },
        { id: 'profile', label: 'Профиль' },
        { id: 'shop', label: 'Магазин' },
      ];
    }

    const counters = (state.me && state.me.counters) || {};

    if (state.cabinetMode === 'own') {
      const items = [
        { id: 'analytics', label: 'Аналитика' },
        { id: 'news', label: 'Новости' },
        { id: 'regions', label: 'Регионы' },
      ];
      // Подтверждение анкет в Mini App — пока только у superuser, обкатываем
      // интерфейс перед тем, как отдавать его federal (см.
      // api/routers/applications.py); в боте подтверждение по-прежнему
      // доступно federal через кнопку «📝 Подтверждения».
      if (state.me && state.me.role === 'superuser') {
        items.push({ id: 'applications', label: 'Заявки', badge: counters.pending_applications });
      }
      items.push({ id: 'bureau', label: 'Бюро' });
      items.push({ id: 'tasks', label: 'Задачи', badge: counters.new_tasks });
      return items;
    }

    // cabinetMode === 'region' — конкретный регион: свой (у leader/cell_leader)
    // или просматриваемый (у federal/coordinator/superuser).
    // Порядок здесь — это и порядок в нижней панели: первые четыре занимают
    // её места, остальные уходят в «Ещё» (см. renderTabs). Поэтому впереди
    // стоит ежедневная работа и то, у чего есть счётчик непрочитанного, а
    // финансы, ячейки, документы и отчёты — то, что открывают периодически.
    const items = [
      { id: 'dashboard', label: 'Сводка' },
      { id: 'members', label: 'Состав', badge: counters.new_purchases },
      { id: 'events', label: 'Мероприятия' },
    ];
    // У federal/coordinator/superuser задачи — в их собственном кабинете
    // (cabinetMode 'own'), не здесь, где они бы мешались с данными
    // просматриваемого региона. У leader/cell_leader отдельного «своего»
    // кабинета нет — задачи остаются прямо тут же, как и раньше.
    if (!hasOwnCabinet()) {
      items.push({ id: 'tasks', label: 'Задачи', badge: counters.new_tasks });
    }
    items.push({ id: 'finance', label: 'Финансы' });
    // Руководитель ячейки работает через эти же вкладки — сервер сам сужает
    // их до его ячейки (см. utils/access.actor_cell). Отдельная вкладка нужна
    // только тем, кто ячейками управляет: руководителю региона и выше.
    if (state.me && state.me.role !== 'cell_leader') {
      items.push({ id: 'cells', label: 'Вузовские ячейки' });
    }
    items.push({ id: 'documents', label: 'Документы' });
    // Отчёты — по региону/ячейке в целом, руководителю ячейки не нужны
    // (у него нет полномочий за пределами своей ячейки).
    if (state.me && state.me.role !== 'cell_leader') {
      items.push({ id: 'reports', label: 'Отчёты' });
    }
    // «Новости»: руководителю региона и ячейки публиковать больше негде,
    // а у federal/coordinator для этого есть свой кабинет.
    if (!hasOwnCabinet()) {
      items.push({ id: 'news', label: 'Новости' });
    }
    return items;
  }

  // Иконки нижней панели. Рисуем контуром (stroke), одним стилем и на сетке
  // 24×24 — так они одинаково читаются и перекрашиваются вместе с текстом
  // через currentColor, в отличие от эмодзи.
  const TAB_ICON_PATHS = {
    home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5.5 9.5V20h13V9.5"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    shield: '<path d="M12 3l7 3v6c0 4.4-3 7.8-7 9-4-1.2-7-4.6-7-9V6z"/>',
    bag: '<path d="M5 8h14l-1 12H6z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>',
    person: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c0-3.6 3.1-6.5 7-6.5s7 2.9 7 6.5"/>',
    chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    people: '<circle cx="9" cy="8" r="3"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><path d="M16 5.5a3 3 0 0 1 0 5.8"/><path d="M17.5 14.2c2 .8 3.5 2.7 3.5 5.3"/>',
    wallet: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18"/><circle cx="17" cy="14.5" r="1"/>',
    checks: '<path d="M9 5h9M9 12h9M9 19h9"/><path d="M4 5.5l1.2 1.2L7.5 4"/><path d="M4 12.5l1.2 1.2L7.5 11"/><path d="M4 19.5l1.2 1.2L7.5 18"/>',
    building: '<path d="M4 20V9l8-5 8 5v11"/><path d="M9 20v-6h6v6"/>',
    doc: '<path d="M6 3h7l5 5v13H6z"/><path d="M13 3v5h5"/>',
    download: '<path d="M12 3v12"/><path d="M8 11l4 4 4-4"/><path d="M4 20h16"/>',
    news: '<path d="M4 5h13v14H4z"/><path d="M17 9h3v8a2 2 0 0 1-3 1.7"/><path d="M7 9h7M7 13h7M7 16h4"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.5 2.5 3.8 5.6 3.8 9S14.5 18.5 12 21c-2.5-2.5-3.8-5.6-3.8-9S9.5 5.5 12 3z"/>',
    inbox: '<path d="M4 13h4l1.5 3h5L16 13h4"/><path d="M4 13 6.5 5h11L20 13v6H4z"/>',
    more: '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>',
    star: '<path d="M12 3l2.4 5 5.6.8-4 3.9.9 5.5-4.9-2.6L7.1 18.2l.9-5.5-4-3.9L9.6 8z"/>',
  };

  const TAB_ICONS = {
    home: 'home',
    dashboard: 'chart',
    members: 'people',
    events: 'calendar',
    myEvents: 'calendar',
    finance: 'wallet',
    cells: 'building',
    documents: 'doc',
    tasks: 'checks',
    reports: 'download',
    analytics: 'chart',
    regions: 'globe',
    applications: 'inbox',
    bureau: 'star',
    news: 'news',
    character: 'shield',
    shop: 'bag',
    profile: 'person',
  };

  function tabIcon(id) {
    const paths = TAB_ICON_PATHS[TAB_ICONS[id] || 'more'] || TAB_ICON_PATHS.more;
    return '<svg class="tab__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths + '</svg>';
  }

  // В панель влезает пять пунктов. Если разделов больше, последнее место
  // занимает «Ещё» — остальные уходят в шторку. Счётчик скрытого раздела не
  // теряется: на «Ещё» загорается точка.
  const NAV_SLOTS = 5;

  // Полоска под активной вкладкой переезжает, а не перескакивает. Живёт
  // отдельным элементом поверх сетки кнопок: сама сетка перерисовывается
  // целиком при каждой смене раздела, и полоска внутри неё анимироваться бы
  // не смогла — новый элемент начинает сразу на месте.
  function moveTabMarker(tabs) {
    let marker = tabs.querySelector('.tabs__marker');
    if (!marker) {
      marker = document.createElement('span');
      marker.className = 'tabs__marker';
      tabs.appendChild(marker);
    }
    const active = tabs.querySelector('.tab--active');
    if (!active) { marker.style.opacity = '0'; return; }
    const width = 22;
    marker.style.opacity = '1';
    marker.style.width = width + 'px';
    marker.style.transform = 'translateX(' +
      Math.round(active.offsetLeft + active.offsetWidth / 2 - width / 2) + 'px)';
  }

  function renderTabs() {
    const items = tabsConfig();
    // В личном кабинете лента новостей — не отдельный пункт панели, а экран,
    // куда уходят с «Главной». Подсвечиваем её, чтобы панель не выглядела
    // так, будто человек нигде.
    let activeId = state.cabinetMode === 'personal' && state.tab === 'news' ? 'home' : state.tab;
    // Страница человека — не вкладка: подсвечиваем ту, с которой на неё ушли.
    if (state.tab === 'person') activeId = state.personBackTab || 'home';
    // Панель всегда из пяти кнопок: не помещается — последняя становится «Ещё».
    const visible = items.length > NAV_SLOTS ? items.slice(0, NAV_SLOTS - 1) : items;
    const hidden = items.length > NAV_SLOTS ? items.slice(NAV_SLOTS - 1) : [];

    let html = visible.map((item) =>
      '<button class="tab' + (activeId === item.id ? ' tab--active' : '') + '" data-tab="' + item.id + '">' +
      tabIcon(item.id) +
      '<span class="tab__label">' + esc(item.label) + '</span>' +
      (item.badge ? '<span class="tab__badge">' + item.badge + '</span>' : '') +
      '</button>').join('');

    if (hidden.length) {
      const activeInside = hidden.some((item) => item.id === activeId);
      const badgeInside = hidden.some((item) => item.badge);
      html += '<button class="tab' + (activeInside ? ' tab--active' : '') + '" id="tabMore">' +
        tabIcon('more') + '<span class="tab__label">Ещё</span>' +
        (badgeInside ? '<span class="tab__dot"></span>' : '') + '</button>';
    }

    const tabs = document.getElementById('tabs');
    // Кнопки меняем, полоску оставляем: если пересоздать и её, она будет
    // каждый раз появляться на новом месте, а не переезжать туда.
    Array.from(tabs.children).forEach((node) => {
      if (!node.classList.contains('tabs__marker')) node.remove();
    });
    tabs.insertAdjacentHTML('afterbegin', html);
    moveTabMarker(tabs);
    on('[data-tab]', 'click', (event) => {
      state.tab = event.currentTarget.dataset.tab;
      renderTabs();
      renderTab();
    }, tabs);
    if (hidden.length) document.getElementById('tabMore').onclick = () => moreSheet(hidden);
  }

  function moreSheet(items) {
    modal('Ещё',
      '<div class="more-list">' + items.map((item) =>
        '<button class="more-list__item' + (state.tab === item.id ? ' more-list__item--active' : '') +
        '" data-more="' + item.id + '">' +
        tabIcon(item.id) +
        '<span class="more-list__label">' + esc(item.label) + '</span>' +
        (item.badge ? '<span class="more-list__badge">' + item.badge + '</span>' : '') +
        '</button>').join('') + '</div>',
      () => {
        on('[data-more]', 'click', (event) => {
          state.tab = event.currentTarget.dataset.more;
          closeModal();
          renderTabs();
          renderTab();
        });
      });
  }

  function ownCabinetLabel() {
    if (state.me.role === 'federal') return 'Кабинет федерального координатора';
    if (state.me.role === 'coordinator') return 'Кабинет координатора регионов';
    return 'Кабинет управления'; // superuser — переходный период, план §6
  }

  // Один список в углу — переключение между личным кабинетом, (у
  // federal/coordinator/superuser) их собственным кабинетом и каждым
  // отдельным регионом по одному — три раздельных пункта, не два, чтобы
  // организационные инструменты не путались с данными одного региона
  // (см. комментарий над tabsConfig).
  // --- Переключение кабинетов ------------------------------------------------
  // Раньше это был системный список <select>: на телефоне он открывает
  // окно операционной системы, выпадает из вида кабинета, и, главное, в него
  // нечего добавить — а добавить нужно. Руководитель отделения он же и
  // участник; сидя в личном кабинете, он не видел, что в управленческом его
  // ждёт задача (вкладки «Задачи» в личном нет вовсе, значок висел только на
  // ней самой). Теперь у каждого кабинета свой счётчик, а на самой кнопке —
  // сумма по остальным: из личного видно, что в другом месте что-то ждёт.

  function cabinetItems() {
    const me = state.me || {};
    const counters = me.counters || {};
    const items = [];

    if (me.has_personal_cabinet) {
      items.push({
        value: 'personal',
        kind: 'personal',
        label: 'Личный кабинет',
        // На кнопке в шапке — коротко: рядом с именем человека места мало, а
        // какого рода кабинет, уже сказал значок.
        short: 'Личный',
        badge: counters.new_tasks || 0,
        active: state.cabinetMode === 'personal',
      });
    }

    if (hasOwnCabinet()) {
      items.push({
        value: 'own',
        kind: 'own',
        label: ownCabinetLabel(),
        short: 'Управление',
        // У federal/coordinator/superuser «Задачи» живут именно здесь.
        badge: (counters.new_tasks || 0) + (counters.pending_applications || 0),
        active: state.cabinetMode === 'own',
      });
    }

    (me.regions || []).forEach((region) => {
      // Руководителю ячейки кабинет подписываем вузом, а не регионом. Регион
      // ему доступен только как рамка вокруг его ячейки: людей всего города
      // он там не увидит, а надпись «Санкт-Петербург» обещала именно их.
      // Регион остаётся в развёрнутом списке — чтобы было видно, где ячейка.
      const cell = me.cell && me.cell.region_id === region.id ? me.cell : null;
      items.push({
        value: 'region:' + region.id,
        kind: 'region',
        label: cell ? cell.name + ' · ' + region.name : region.name,
        short: cell ? cell.name : region.name,
        // У руководителя отделения своего «верхнего» кабинета нет, и задачи
        // лежат прямо здесь — значит и считать их надо здесь.
        badge: (counters.new_purchases || 0) + (hasOwnCabinet() ? 0 : counters.new_tasks || 0),
        active: state.cabinetMode === 'region' && region.id === state.regionId,
      });
    });

    return items;
  }

  function cabinetIcon(kind) {
    const paths = {
      personal: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM5 20c1.2-3.2 4-5 7-5s5.8 1.8 7 5',
      own: 'M12 3l2.4 5 5.6.8-4 3.9.9 5.5-4.9-2.6L7.1 18.2l.9-5.5-4-3.9L9.6 8z',
      region: 'M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
    };
    return '<svg class="cabinet__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="' +
      (paths[kind] || paths.region) + '"></path></svg>';
  }

  function renderCabinetSwitch() {
    const button = document.getElementById('cabinetSwitch');
    const items = cabinetItems();
    // Один кабинет — переключать не на что, кнопке в шапке делать нечего.
    if (items.length <= 1) {
      button.hidden = true;
      return;
    }
    button.hidden = false;

    const current = items.find((item) => item.active) || items[0];
    // На кнопке — то, что ждёт в ДРУГИХ кабинетах: про текущий человек и так
    // всё видит по значкам на вкладках.
    const elsewhere = items.reduce((sum, item) => sum + (item.active ? 0 : item.badge), 0);

    button.innerHTML =
      cabinetIcon(current.kind) +
      '<span class="cabinet__label">' + esc(current.short || current.label) + '</span>' +
      (elsewhere ? '<span class="cabinet__badge">' + elsewhere + '</span>' : '') +
      '<svg class="cabinet__chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10l5 5 5-5"></path></svg>';

    button.onclick = cabinetSheet;
  }

  function cabinetSheet() {
    const items = cabinetItems();
    modal('Кабинеты',
      '<div class="more-list">' + items.map((item) =>
        '<button type="button" class="more-list__item cabinet-row' +
        (item.active ? ' more-list__item--active' : '') +
        '" data-cabinet="' + esc(item.value) + '">' +
        cabinetIcon(item.kind) +
        '<span class="more-list__label">' + esc(item.label) + '</span>' +
        (item.badge ? '<span class="more-list__badge">' + item.badge + '</span>' : '') +
        (item.active
          ? '<svg class="cabinet-row__check" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"></path></svg>'
          : '') +
        '</button>').join('') + '</div>',
      () => {
        on('[data-cabinet]', 'click', (event) => {
          closeModal();
          switchCabinet(event.currentTarget.dataset.cabinet);
        });
      });
  }

  function switchCabinet(value) {
    if (value === 'personal') {
      state.cabinetMode = 'personal';
      state.tab = 'home';
      // Личный кабинет — подпись под именем сразу статус (корпорант/член
      // Братства/выпускник), а не орг-роль, с самого первого экрана
      // («Академия»), не только после «Личной информации» (см. renderProfile).
      document.getElementById('userRole').textContent = '';
    } else if (value === 'own') {
      state.cabinetMode = 'own';
      state.tab = 'analytics';
      // В личном кабинете подпись под именем — статус; вернулись сюда — снова роль.
      document.getElementById('userRole').textContent = state.me.role_label || '';
    } else {
      state.cabinetMode = 'region';
      state.regionId = Number(value.split(':')[1]);
      state.tab = 'dashboard';
      document.getElementById('userRole').textContent = state.me.role_label || '';
    }
    renderCabinetSwitch();
    renderTabs();
    renderTab();
  }

  function renderTab() {
    const renderers = {
      dashboard: renderDashboard,
      members: renderMembers,
      finance: renderFinance,
      events: renderEvents,
      documents: renderDocuments,
      cells: renderCells,
      tasks: renderTasks,
      analytics: renderAnalytics,
      reports: renderReports,
      profile: renderProfile,
      home: renderHome,
      person: renderPersonPage,
      bureau: renderBureau,
      news: renderNews,
      regions: renderRegionsTab,
      applications: renderApplicationsTab,
      character: renderCharacter,
      shop: renderShop,
      myEvents: renderParticipantEvents,
    };
    const renderer = renderers[state.tab] || renderDashboard;
    startRender((gen) => {
      setView('<div class="loader">Загрузка…</div>', gen);
      return renderer(gen);
    });
  }

  function needRegion(gen) {
    if (state.regionId) return false;
    setView('<div class="empty">Регион не назначен. Обратитесь к координатору.</div>', gen);
    return true;
  }

  // --- Сводка ---------------------------------------------------------------

  async function renderDashboard(gen) {
    if (needRegion(gen)) return;
    const data = await api('/dashboard?region_id=' + state.regionId);

    const statuses = data.members.by_status.map((row) =>
      '<div class="row"><div class="row__main"><div class="row__title">' + esc(row.label) + '</div></div>' +
      '<div class="row__side">' + row.count + '</div></div>').join('');

    const events = data.upcoming_events.length
      ? data.upcoming_events.map((event) =>
        '<div class="row"><div class="row__main"><div class="row__title">' + esc(event.title) + '</div>' +
        '<div class="row__sub">' + dateRu(event.date) + (event.time ? ', ' + event.time : '') + '</div></div>' +
        '</div>').join('')
      : '<div class="empty">Ближайших мероприятий нет</div>';

    const birthdays = data.birthdays.length
      ? data.birthdays.map((person) =>
        '<div class="row"><div class="row__main"><div class="row__title">' + esc(person.full_name) + '</div>' +
        '<div class="row__sub">' + dateRu(person.date) + ' · ' + person.turns + ' лет</div></div></div>').join('')
      : '<div class="empty">В ближайшие две недели дней рождения нет</div>';

    setView(
      '<div class="grid">' +
      '<div class="stat"><div class="stat__label">Баланс региона</div>' +
      '<div class="stat__value">' + money(data.finance.balance) + '</div></div>' +
      '<div class="stat"><div class="stat__label">Состав</div>' +
      '<div class="stat__value">' + data.members.total + '</div></div>' +
      '<div class="stat"><div class="stat__label">Доходы · ' + esc(data.finance.period_label) + '</div>' +
      '<div class="stat__value stat__value--income">' + money(data.finance.period_income) + '</div></div>' +
      '<div class="stat"><div class="stat__label">Расходы · ' + esc(data.finance.period_label) + '</div>' +
      '<div class="stat__value stat__value--expense">' + money(data.finance.period_expense) + '</div></div>' +
      '<div class="stat"><div class="stat__label">Новых в этом месяце</div>' +
      '<div class="stat__value stat__value--income">' + (data.members.new_this_month || 0) + '</div></div>' +
      '</div>' +

      (data.counters.new_tasks
        ? '<div class="card"><div class="card__title">Требует внимания</div>' +
          '<div class="row"><div class="row__main">Новых задач</div><div class="row__side">' + data.counters.new_tasks + '</div></div>' +
          '</div>'
        : '') +

      '<div class="card"><div class="card__title">Состав по статусам</div>' + statuses + '</div>' +
      '<div class="card"><div class="card__title">Ближайшие мероприятия</div>' + events + '</div>' +
      '<div class="card"><div class="card__title">Дни рождения</div>' + birthdays + '</div>'
    , gen);

  }

  // --- Состав ---------------------------------------------------------------

  async function renderMembers(gen) {
    if (needRegion(gen)) return;
    const params = new URLSearchParams({ region_id: state.regionId });
    if (state.membersFilter.q) params.set('q', state.membersFilter.q);
    if (state.membersFilter.status) params.set('status', state.membersFilter.status);
    const data = await api('/members?' + params.toString());

    const chips = '<div class="chips">' +
      '<button class="chip' + (state.membersFilter.status === '' ? ' chip--active' : '') + '" data-status="">Все · ' + data.total + '</button>' +
      data.counts.map((row) =>
        '<button class="chip' + (state.membersFilter.status === row.status ? ' chip--active' : '') +
        '" data-status="' + row.status + '">' + esc(row.label) + ' · ' + row.count + '</button>').join('') +
      '</div>';

    const rows = data.items.length ? data.items.map((member) =>
      '<div class="row row--clickable" data-member="' + member.id + '">' +
      '<div class="row__main"><div class="row__title">' + esc(member.full_name) +
      (member.has_unseen_purchases ? ' <span class="dot dot--alert"></span>' : '') + '</div>' +
      '<div class="row__sub">' + esc(member.status_label) +
      (member.phone ? ' · ' + esc(member.phone) : '') +
      (member.birth_date ? ' · ' + dateRu(member.birth_date) : '') + '</div>' +
      (memberEducationLine(member) ? '<div class="row__sub">' + memberEducationLine(member) + '</div>' : '') +
      '</div>' +
      '</div>').join('') : '<div class="empty">Никого не найдено</div>';

    setView(
      '<div class="field"><input id="memberSearch" placeholder="Поиск по ФИО" value="' + esc(state.membersFilter.q) + '" /></div>' +
      chips +
      '<div class="card">' + rows + '</div>'
    , gen);

    const search = document.getElementById('memberSearch');
    let timer = null;
    search.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => { state.membersFilter.q = search.value.trim(); startRender(renderMembers); }, 350);
    });
    on('.chip', 'click', (event) => {
      state.membersFilter.status = event.currentTarget.dataset.status;
      startRender(renderMembers);
    });
    on('[data-member]', 'click', (event) => {
      const member = data.items.find((m) => m.id === Number(event.currentTarget.dataset.member));
      if (member) memberForm(member).catch(fail);
    });
  }

  function memberEducationLine(member) {
    if (member.university_name) {
      return esc([
        member.university_name,
        member.faculty,
        member.course ? member.course + ' курс' : null,
        member.education_level_label,
      ].filter(Boolean).join(', '));
    }
    if (member.workplace) return 'Место работы: ' + esc(member.workplace);
    return '';
  }

  async function memberForm(member) {
    const statuses = state.me.dictionaries.member_statuses;
    const editable = canEdit();
    modal(member ? 'Карточка человека' : 'Новый человек',
      '<div class="field"><label>ФИО</label><input id="mName" value="' + esc(member ? member.full_name : '') + '" ' + (editable ? '' : 'disabled') + ' /></div>' +
      '<div class="field"><label>Статус</label><select id="mStatus" ' + (editable ? '' : 'disabled') + '>' +
      statuses.map((s) => '<option value="' + s.value + '"' + (member && member.status === s.value ? ' selected' : '') + '>' + esc(s.label) + '</option>').join('') +
      '</select></div>' +
      '<div class="field"><label>Телефон</label><input id="mPhone" value="' + esc(member ? member.phone || '' : '') + '" ' + (editable ? '' : 'disabled') + ' /></div>' +
      '<div class="field"><label>Telegram</label><input id="mTelegram" placeholder="@qwerty" value="' + esc(member ? member.telegram_username || '' : '') + '" ' + (editable ? '' : 'disabled') + ' /></div>' +
      '<div class="field"><label>Дата рождения</label><input id="mBirth" placeholder="20.02.2000" inputmode="numeric" value="' + esc(member ? isoToRuDate(member.birth_date) : '') + '" ' + (editable ? '' : 'disabled') + ' /></div>' +
      '<div class="field" id="mActivistJoinedField"><label>Вступление в Академисты</label><input id="mActivistJoined" placeholder="20.02.2000" inputmode="numeric" value="' + esc(member ? isoToRuDate(member.activist_joined_at) : '') + '" ' + (editable ? '' : 'disabled') + ' /></div>' +
      '<div class="field" id="mMemberInductedField" hidden><label>Посвящение в Братство</label><input id="mMemberInducted" placeholder="20.02.2000" inputmode="numeric" value="' + esc(member ? isoToRuDate(member.member_inducted_at) : '') + '" ' + (editable ? '' : 'disabled') + ' /></div>' +
      '<div class="field" id="mAlumniGraduatedField" hidden><label>Выпуск из студенческого Братства</label><input id="mAlumniGraduated" placeholder="20.02.2000" inputmode="numeric" value="' + esc(member ? isoToRuDate(member.alumni_graduated_at) : '') + '" ' + (editable ? '' : 'disabled') + ' /></div>' +
      // ВУЗ — поиск по каталогу с автодополнением; ячейка производная от него,
      // отдельного выбора ячейки в форме больше нет (см. utils/university_cells.py).
      '<div class="field"><label>ВУЗ</label><input id="mUniversityInput" placeholder="Начните вводить название" autocomplete="off" value="' +
        esc(member && member.university_name ? member.university_name : '') + '" ' + (editable ? '' : 'disabled') + ' />' +
        '<div id="mUniversitySuggestions" class="chips" style="margin-top:var(--space-8)"></div>' +
        '</div>' +
      '<div class="field"><label>Факультет</label><input id="mFaculty" value="' + esc(member ? member.faculty || '' : '') + '" ' + (editable ? '' : 'disabled') + ' /></div>' +
      '<div class="field"><label>Курс</label><select id="mCourse" ' + (editable ? '' : 'disabled') + '>' +
      '<option value="">—</option>' +
      [1, 2, 3, 4, 5, 6].map((n) =>
        '<option value="' + n + '"' + (member && member.course === n ? ' selected' : '') + '>' + n + '</option>').join('') +
      '<option value="graduated"' + (member && member.graduated_university ? ' selected' : '') + '>Окончил</option>' +
      '</select></div>' +
      '<div class="field" id="mEducationLevelField"' + (member && member.graduated_university ? ' hidden' : '') + '><label>Уровень обучения</label><select id="mEducationLevel" ' + (editable ? '' : 'disabled') + '>' +
      '<option value="">—</option>' +
      ['bachelor:Бакалавриат', 'master:Магистратура', 'postgraduate:Аспирантура', 'residency:Ординатура'].map((pair) => {
        const [value, label] = pair.split(':');
        return '<option value="' + value + '"' + (member && member.education_level === value ? ' selected' : '') + '>' + label + '</option>';
      }).join('') +
      '</select></div>' +
      '<div class="field"><label>Место работы</label><input id="mWorkplace" value="' + esc(member ? member.workplace || '' : '') + '" ' + (editable ? '' : 'disabled') + ' /></div>' +
      '<div class="field"><label>Комментарий</label><input id="mComment" value="' + esc(member ? member.comment || '' : '') + '" ' + (editable ? '' : 'disabled') + ' /></div>' +
      '<div class="btn-row">' +
      (editable ? '<button class="btn" id="mSave">Сохранить</button>' : '') +
      (member ? '<button class="btn btn--ghost" id="mQuests">Задания</button>' : '') +
      (member ? '<button class="btn btn--ghost" id="mPurchases">Покупки' +
        (member.has_unseen_purchases ? ' <span class="dot dot--alert"></span>' : '') + '</button>' : '') +
      (editable && member ? '<button class="btn btn--danger" id="mDelete">Исключить</button>' : '') +
      '<button class="btn btn--ghost" id="mClose">Закрыть</button></div>',
      () => {
        applyPhoneMask(document.getElementById('mPhone'));
        applyDateMask(document.getElementById('mBirth'));
        applyDateMask(document.getElementById('mActivistJoined'));
        applyDateMask(document.getElementById('mMemberInducted'));
        applyDateMask(document.getElementById('mAlumniGraduated'));

        // Поле следующей вехи появляется только после того, как руководитель
        // выставит соответствующий статус — до этого его не видно совсем,
        // не просто disabled (план, п.8).
        const STATUS_ORDER = ['activist', 'member', 'alumni'];
        const statusSelect = document.getElementById('mStatus');
        const memberInductedField = document.getElementById('mMemberInductedField');
        const alumniGraduatedField = document.getElementById('mAlumniGraduatedField');
        function updateMilestoneFields() {
          const index = STATUS_ORDER.indexOf(statusSelect.value);
          memberInductedField.hidden = index < 1;
          alumniGraduatedField.hidden = index < 2;
        }
        updateMilestoneFields();

        document.getElementById('mClose').onclick = closeModal;
        if (member) document.getElementById('mQuests').onclick = () => memberQuestsModal(member, editable);
        if (member) document.getElementById('mPurchases').onclick = () => memberPurchasesModal(member, editable);
        if (!editable) return;

        statusSelect.addEventListener('change', updateMilestoneFields);

        let selectedUniversityId = member ? member.university_id : null;
        const uniInput = document.getElementById('mUniversityInput');
        const uniSuggestions = document.getElementById('mUniversitySuggestions');
        let uniTimer;
        uniInput.addEventListener('input', () => {
          selectedUniversityId = null; // руками правили — досоздастся/найдётся по имени при сохранении
          clearTimeout(uniTimer);
          const q = uniInput.value.trim();
          if (!q) { uniSuggestions.innerHTML = ''; return; }
          uniTimer = setTimeout(async () => {
            try {
              const data = await api('/universities?q=' + encodeURIComponent(q));
              uniSuggestions.innerHTML = data.items.map((u) =>
                '<button type="button" class="chip" data-uni="' + u.id + '" data-name="' + esc(u.name) + '">' + esc(u.name) + '</button>'
              ).join('');
              on('[data-uni]', 'click', (event) => {
                selectedUniversityId = Number(event.currentTarget.dataset.uni);
                uniInput.value = event.currentTarget.dataset.name;
                uniSuggestions.innerHTML = '';
              }, uniSuggestions);
            } catch (error) { /* автодополнение необязательно — молча пропускаем сбой */ }
          }, 300);
        });

        const courseSelect = document.getElementById('mCourse');
        const educationLevelField = document.getElementById('mEducationLevelField');
        courseSelect.addEventListener('change', () => {
          const graduated = courseSelect.value === 'graduated';
          educationLevelField.hidden = graduated;
          if (graduated) document.getElementById('mEducationLevel').value = '';
        });

        document.getElementById('mSave').onclick = async () => {
          const status = document.getElementById('mStatus').value;

          const dateInputs = [
            ['mBirth', 'Дата рождения'],
            ['mActivistJoined', 'Вступление в Академисты'],
            ['mMemberInducted', 'Посвящение в Братство'],
            ['mAlumniGraduated', 'Выпуск из студенческого Братства'],
          ];
          for (const [id, label] of dateInputs) {
            const raw = document.getElementById(id).value.trim();
            if (raw && !ruDateToIso(raw)) { toast('Проверьте дату «' + label + '» — формат ДД.ММ.ГГГГ'); return; }
          }
          const birthDate = ruDateToIso(document.getElementById('mBirth').value);
          const activistJoinedAt = ruDateToIso(document.getElementById('mActivistJoined').value);
          const memberInductedAt = ruDateToIso(document.getElementById('mMemberInducted').value);
          const alumniGraduatedAt = ruDateToIso(document.getElementById('mAlumniGraduated').value);
          // Веха обязательна для статуса, который ей соответствует — руководитель
          // присваивает статус члена/выпускника осознанно, вместе с датой события.
          if (status === 'member' && !memberInductedAt) { toast('Укажите дату посвящения в Братство'); return; }
          if (status === 'alumni' && !alumniGraduatedAt) { toast('Укажите дату выпуска из студенческого Братства'); return; }

          const courseRaw = document.getElementById('mCourse').value;
          const graduatedUniversity = courseRaw === 'graduated';
          const workplace = document.getElementById('mWorkplace').value.trim() || null;
          if (graduatedUniversity && !workplace) { toast('Укажите место работы'); return; }

          const uniName = uniInput.value.trim();
          const uniProblem = universityNameProblem(uniName);
          if (uniProblem) { toast(uniProblem); return; }
          let universityId = selectedUniversityId;
          if (!uniName) {
            universityId = null;
          } else if (!universityId) {
            try {
              const created = await api('/universities', { method: 'POST', body: { name: uniName } });
              universityId = created.id;
            } catch (error) { fail(error); return; }
          }
          const payload = {
            full_name: document.getElementById('mName').value.trim(),
            status: status,
            phone: document.getElementById('mPhone').value.trim() || null,
            telegram_username: document.getElementById('mTelegram').value.trim() || null,
            birth_date: birthDate,
            activist_joined_at: activistJoinedAt,
            member_inducted_at: memberInductedAt,
            alumni_graduated_at: alumniGraduatedAt,
            comment: document.getElementById('mComment').value.trim() || null,
            university_id: universityId,
            faculty: document.getElementById('mFaculty').value.trim() || null,
            course: graduatedUniversity ? null : (courseRaw ? Number(courseRaw) : null),
            graduated_university: graduatedUniversity,
            education_level: graduatedUniversity ? null : (document.getElementById('mEducationLevel').value || null),
            workplace: workplace,
          };
          try {
            if (member) await api('/members/' + member.id, { method: 'PATCH', body: payload });
            else await api('/members', { method: 'POST', body: Object.assign({ region_id: state.regionId }, payload) });
            closeModal();
            toast('Сохранено');
            startRender(renderMembers);
          } catch (error) { fail(error); }
        };
        if (member) {
          document.getElementById('mDelete').onclick = () => confirmAction(
            'Исключить безвозвратно? Человек и его личный кабинет (если есть) будут удалены полностью — это необратимо.',
            async () => {
              try {
                await api('/members/' + member.id, { method: 'DELETE' });
                closeModal();
                startRender(renderMembers);
              } catch (error) { fail(error); }
            });
        }
      });
  }

  // Задания геймификации (план «Персонаж и инвентарь», MVP). Руководитель
  // баллов не начисляет: «+1» двигает счётчик выполнения, а звёзды человек
  // забирает сам в «Академии» (api/routers/quests.py). Раньше экран об этом
  // молчал — руководитель жал кнопку и не видел ни своего действия, ни его
  // последствий, ни баланса человека. Отсюда и «непонятно, начислил ли».
  async function memberQuestsModal(member, editable) {
    // Что сделано в этом окне: отметок и открывшихся звёзд по каждому
    // заданию. Живёт до закрытия — чтобы итог не пропал из виду.
    const session = new Map();

    const draw = async () => {
      const data = await api('/members/' + member.id + '/quests');
      const body = document.getElementById('mqBody');
      if (!body) return;

      const marked = [...session.values()].reduce((n, x) => n + x.marks, 0);
      const opened = [...session.values()].reduce((n, x) => n + x.stars, 0);

      body.innerHTML =
        '<div class="card duty">' + avatarHtml(null, member.full_name) +
        '<span class="duty__body" style="margin-left:var(--space-8)">' +
        '<span class="duty__name">' + esc(member.full_name) + '</span>' +
        '<span class="row__sub">' + esc(member.status_label || '') + '</span></span></div>' +

        // Баланс человека. Этих цифр не было нигде.
        '<div class="stats2">' +
        '<div class="card stat2"><div class="row__sub">Уже забрал</div>' +
        '<div class="stat2__value">' + data.claimed + ' ★</div></div>' +
        '<div class="card stat2"><div class="row__sub">Ждёт в Академии</div>' +
        '<div class="stat2__value' + (data.pending ? ' stat2__value--accent' : '') + '">' + data.pending + ' ★</div></div>' +
        '</div>' +

        (marked
          ? '<div class="notice notice--done">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"></path></svg>' +
            '<div><div>' + esc(declOtmetka(marked) + (opened ? ' — открыто ' + opened + ' ★' : '')) + '</div>' +
            '<div class="row__sub">' + (opened
              ? 'Человеку ушло уведомление в бот'
              : 'Ни одна ступень пока не пройдена — звёзд не прибавилось') + '</div></div></div>'
          : '') +

        groupQuestsByBranch(data.items).map((g) =>
          '<div class="section-title">' + esc(g.label) + '</div>' +
          '<div class="card card--rows">' + g.quests.map((q) => questRow(q)).join('') + '</div>'
        ).join('') +

        '<div class="notice">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 8v5M12 16v.01"></path></svg>' +
        '<div>Звёзды человек забирает сам — в «Академии», кнопкой «Получить». Поэтому «уже забрал» меняется не сейчас, а когда он туда зайдёт.</div></div>' +

        '<div class="btn-row"><button class="btn btn--ghost" id="mqClose">Закрыть</button></div>';

      wire(data);
    };

    const questRow = (q) => {
      const was = session.get(q.id);
      const done = q.next_target === null;
      const pct = done ? 100 : Math.round((q.count / q.next_target) * 100);
      return '<div class="row quest"><div class="row__main">' +
        '<div class="row__title">' + esc(q.title) + '</div>' +
        '<div class="quest-bar"><div class="quest-bar__fill' + (done ? ' quest-bar__fill--done' : '') +
        '" style="width:' + pct + '%"></div></div>' +
        // Сколько до ступени и что она даст: награда равна номеру ступени
        // (api/routers/character.py::_earned_stars).
        '<div class="row__sub">' + esc(done
          ? 'Выполнено полностью · отмечено ' + q.count
          : q.count + ' из ' + q.next_target + ' — на ступени откроется ' + q.next_reward + ' ★') + '</div>' +
        (was
          ? '<div class="quest__note">' +
            '<span class="' + (was.stars ? 'quest__note--good' : 'row__sub') + '">' +
            esc(declOtmetka(was.marks) + (was.stars ? ' — открылось ' + was.stars + ' ★' : '')) + '</span>' +
            (editable ? '<button type="button" class="duty__act" data-quest-undo="' + q.id + '">Отменить</button>' : '') +
            '</div>'
          : '') +
        '</div>' +
        (editable
          ? '<div class="row__side"><button class="btn btn--small" data-quest-mark="' + q.id + '">' +
            (done ? 'Ещё' : 'Отметить') + '</button></div>'
          : '') +
        '</div>';
    };

    // Открывшиеся звёзды считаем по ответу сервера: он присылает состояние
    // задания после нажатия, а разницу берём с состоянием до него.
    const step = async (questId, path, sign, data) => {
      const before = data.items.find((q) => q.id === questId);
      const earnedBefore = before.claimable_stars + before.stars_claimed;
      try {
        const after = await api('/members/' + member.id + '/quests/' + questId + '/' + path, { method: 'POST' });
        const gained = (after.claimable_stars + after.stars_claimed) - earnedBefore;
        const was = session.get(questId) || { marks: 0, stars: 0 };
        const marks = was.marks + sign;
        if (marks <= 0) session.delete(questId);
        else session.set(questId, { marks: marks, stars: Math.max(0, was.stars + gained) });
        await draw();
      } catch (error) { fail(error); }
    };

    const wire = (data) => {
      document.getElementById('mqClose').onclick = closeModal;
      on('[data-quest-mark]', 'click', (event) =>
        step(Number(event.currentTarget.dataset.questMark), 'increment', 1, data));
      on('[data-quest-undo]', 'click', (event) =>
        step(Number(event.currentTarget.dataset.questUndo), 'decrement', -1, data));
    };

    modal('Задания', '<div id="mqBody" class="stack"><div class="loader">Загружаю…</div></div>', () => { draw().catch(fail); });
  }

  function declOtmetka(n) {
    const tail = n % 100 >= 11 && n % 100 <= 14 ? 0 : n % 10;
    return 'Отмечено ' + n + (tail === 1 ? ' раз' : (tail >= 2 && tail <= 4 ? ' раза' : ' раз'));
  }

  async function memberPurchasesModal(member, editable) {
    const data = await api('/members/' + member.id + '/shop-purchases');
    const rows = data.items.map((p) =>
      '<div class="row"><div class="row__main"><div class="row__title">' + esc(p.label) + '</div>' +
      '<div class="row__sub">' + p.price_stars + ' ⭐' + (p.kind === 'physical' ? ' · материальная награда' : '') + '</div></div>' +
      '<div class="row__side">' +
      (p.kind !== 'physical' ? '<span class="row__sub">выдано автоматически</span>'
        : p.fulfilled ? '<span class="row__sub">выдано</span>'
        : editable ? '<button class="btn btn--small" data-fulfill-purchase="' + p.id + '">Отметить выдачу</button>'
        : '<span class="row__sub">не выдано</span>') +
      '</div></div>'
    ).join('');

    modal('Покупки — ' + member.full_name,
      '<div class="card">' + (rows || '<div class="empty">Пока ничего не куплено</div>') + '</div>' +
      '<div class="btn-row"><button class="btn btn--ghost" id="mpClose">Закрыть</button></div>',
      () => {
        document.getElementById('mpClose').onclick = closeModal;
        on('[data-fulfill-purchase]', 'click', async (event) => {
          const purchaseId = Number(event.currentTarget.dataset.fulfillPurchase);
          try {
            await api('/members/' + member.id + '/shop-purchases/' + purchaseId + '/fulfill', { method: 'POST' });
            memberPurchasesModal(member, editable).catch(fail);
          } catch (error) { fail(error); }
        });
      });
  }

  // --- Финансы --------------------------------------------------------------

  function periodControls(period, prefix) {
    const kinds = [['month', 'Месяц'], ['semester', 'Семестр'], ['year', 'Год'], ['all', 'Всё время']];
    return '<div class="chips">' +
      kinds.map((k) => '<button class="chip' + (period.kind === k[0] ? ' chip--active' : '') +
        '" data-' + prefix + '-kind="' + k[0] + '">' + k[1] + '</button>').join('') +
      (period.kind === 'all' ? '' :
        '<button class="chip" data-' + prefix + '-shift="-1">‹ Пред.</button>' +
        '<button class="chip" data-' + prefix + '-shift="1">След. ›</button>') +
      '</div>';
  }

  function bindPeriod(period, prefix, rerender) {
    on('[data-' + prefix + '-kind]', 'click', (event) => {
      period.kind = event.currentTarget.dataset[prefix + 'Kind'];
      period.offset = 0;
      rerender();
    });
    on('[data-' + prefix + '-shift]', 'click', (event) => {
      period.offset += Number(event.currentTarget.dataset[prefix + 'Shift']);
      rerender();
    });
  }

  async function renderFinance(gen) {
    if (needRegion(gen)) return;
    const period = state.financePeriod;
    const query = 'region_id=' + state.regionId + '&period=' + period.kind + '&offset=' + period.offset;
    const [overview, transactions] = await Promise.all([
      api('/finance/overview?' + query),
      api('/finance/transactions?' + query + '&limit=60'),
    ]);

    const breakdown = overview.expense_by_category.length
      ? overview.expense_by_category.map((row) => {
        const share = overview.expense ? Math.round((row.amount / overview.expense) * 100) : 0;
        return '<div class="row"><div class="row__main"><div class="row__title">' +
          esc((row.emoji ? row.emoji + ' ' : '') + row.name) + '</div>' +
          '<div class="progress"><div class="progress__bar" style="width:' + share + '%"></div></div></div>' +
          '<div class="row__side">' + money(row.amount) + '<div class="row__sub">' + share + '%</div></div></div>';
      }).join('')
      : '<div class="empty">Расходов за период нет</div>';

    const list = transactions.items.length ? transactions.items.map((tx) =>
      '<div class="row row--clickable" data-tx="' + tx.id + '">' +
      '<div class="row__main"><div class="row__title">' + esc(tx.category_name || 'Без категории') + '</div>' +
      '<div class="row__sub">' + dateRu(tx.date) +
      (tx.event_title ? ' · 🎪 ' + esc(tx.event_title) : '') +
      (tx.comment ? ' · ' + esc(tx.comment) : '') +
      (tx.author_name ? ' · ' + esc(tx.author_name) : '') + '</div></div>' +
      '<div class="row__side amount amount--' + tx.type + '">' +
      (tx.type === 'income' ? '+' : '−') + money(tx.amount) + '</div></div>').join('')
      : '<div class="empty">Операций за период нет</div>';

    setView(
      periodControls(period, 'fin') +
      '<div class="grid">' +
      '<div class="stat"><div class="stat__label">Баланс (всего)</div><div class="stat__value">' + money(overview.balance) + '</div></div>' +
      '<div class="stat"><div class="stat__label">Доходы · ' + esc(overview.period.label) + '</div>' +
      '<div class="stat__value stat__value--income">' + money(overview.income) + '</div></div>' +
      '<div class="stat"><div class="stat__label">Расходы · ' + esc(overview.period.label) + '</div>' +
      '<div class="stat__value stat__value--expense">' + money(overview.expense) + '</div></div>' +
      '<div class="stat"><div class="stat__label">Итог периода</div><div class="stat__value">' + money(overview.net) + '</div></div>' +
      '</div>' +
      (canEdit()
        ? '<div class="btn-row"><button class="btn" id="addTx">➕ Операция</button>' +
          '<button class="btn btn--ghost" id="manageCats">📁 Категории</button>' +
          '<button class="btn btn--ghost" id="exportCsv">⬇️ CSV</button></div>'
        : '<div class="btn-row"><button class="btn btn--ghost" id="exportCsv">⬇️ CSV</button></div>') +
      '<div class="card"><div class="card__title">Расходы по категориям</div>' + breakdown + '</div>' +
      '<div class="card"><div class="card__title">Операции · ' + esc(transactions.period_label) + '</div>' + list + '</div>'
    , gen);

    bindPeriod(period, 'fin', () => startRender(renderFinance));
    document.getElementById('exportCsv').onclick = () =>
      download('/finance/export.csv?' + query, 'finance.csv').catch(fail);
    if (canEdit()) {
      document.getElementById('addTx').onclick = () => transactionForm();
      document.getElementById('manageCats').onclick = () => categoriesModal().catch(fail);
      on('[data-tx]', 'click', (event) => {
        const id = Number(event.currentTarget.dataset.tx);
        confirmAction('Удалить операцию?', async () => {
          try {
            await api('/finance/transactions/' + id, { method: 'DELETE' });
            startRender(renderFinance);
          } catch (error) { fail(error); }
        });
      });
    }
  }

  // Категории, подходящие под тип операции — доход и расход не мешаются
  // в одном списке (иначе к расходу можно было бы случайно привязать
  // категорию дохода и наоборот).
  function categoryOptionsHtml(categories, type) {
    return '<option value="">Без категории</option>' +
      categories.filter((c) => c.type === type).map((c) =>
        '<option value="' + c.id + '">' + esc((c.emoji ? c.emoji + ' ' : '') + c.name) + '</option>'
      ).join('');
  }

  async function transactionForm() {
    const [categories, events] = await Promise.all([
      api('/finance/categories?region_id=' + state.regionId),
      api('/events?region_id=' + state.regionId + '&scope=all'),
    ]);

    modal('Новая операция',
      '<div class="field"><label>Тип</label><select id="txType">' +
      '<option value="expense">Расход</option><option value="income">Доход</option></select></div>' +
      '<div class="field"><label>Сумма, ₽</label><input id="txAmount" inputmode="decimal" placeholder="2500" /></div>' +
      '<div class="field"><label>Категория</label><select id="txCategory">' + categoryOptionsHtml(categories.items, 'expense') + '</select></div>' +
      '<div class="field"><label>Мероприятие (необязательно)</label><select id="txEvent"><option value="">—</option>' +
      events.items.map((e) => '<option value="' + e.id + '">' + esc(e.title) + ' · ' + dateShort(e.date) + '</option>').join('') +
      '</select></div>' +
      '<div class="field"><label>Дата</label><input id="txDate" type="date" value="' + todayIsoLocal() + '" /></div>' +
      '<div class="field"><label>Комментарий</label><input id="txComment" /></div>' +
      '<div class="btn-row"><button class="btn" id="txSave">Записать</button>' +
      '<button class="btn btn--ghost" id="txClose">Отмена</button></div>',
      () => {
        const typeSelect = document.getElementById('txType');
        const categorySelect = document.getElementById('txCategory');
        typeSelect.onchange = () => {
          categorySelect.innerHTML = categoryOptionsHtml(categories.items, typeSelect.value);
        };
        document.getElementById('txClose').onclick = closeModal;
        document.getElementById('txSave').onclick = async () => {
          const rubles = document.getElementById('txAmount').value.replace(',', '.').trim();
          const amount = Math.round(parseFloat(rubles) * 100);
          if (!amount || amount <= 0 || Number.isNaN(amount)) { toast('Введите сумму'); return; }
          try {
            await api('/finance/transactions', {
              method: 'POST',
              body: {
                region_id: state.regionId,
                amount: amount,
                type: typeSelect.value,
                category_id: categorySelect.value ? Number(categorySelect.value) : null,
                event_id: document.getElementById('txEvent').value ? Number(document.getElementById('txEvent').value) : null,
                date: document.getElementById('txDate').value || null,
                comment: document.getElementById('txComment').value.trim() || null,
              },
            });
            closeModal();
            toast('Записано');
            startRender(renderFinance);
          } catch (error) { fail(error); }
        };
      });
  }

  async function categoriesModal() {
    const data = await api('/finance/categories?region_id=' + state.regionId);
    const rows = data.items.map((c) =>
      '<div class="row"><div class="row__main"><div class="row__title">' +
      esc((c.emoji ? c.emoji + ' ' : '') + c.name) + '</div>' +
      '<div class="row__sub">' + (c.type === 'income' ? 'Доход' : 'Расход') +
      (c.keywords.length ? ' · ' + esc(c.keywords.join(', ')) : '') + '</div></div>' +
      '<div class="row__side"><button class="btn btn--ghost btn--small" data-del-cat="' + c.id + '">✕</button></div></div>').join('');

    modal('Категории',
      '<div class="card">' + (rows || '<div class="empty">Категорий нет</div>') + '</div>' +
      '<div class="section-title">Новая категория</div>' +
      '<div class="field"><input id="catName" placeholder="Название" /></div>' +
      '<div class="field"><select id="catType"><option value="expense">Расход</option><option value="income">Доход</option></select></div>' +
      '<div class="field"><input id="catEmoji" placeholder="Эмодзи (необязательно)" /></div>' +
      '<div class="field"><input id="catWords" placeholder="Ключевые слова через запятую" /></div>' +
      '<div class="btn-row"><button class="btn" id="catAdd">Добавить</button>' +
      '<button class="btn btn--ghost" id="catClose">Закрыть</button></div>',
      () => {
        document.getElementById('catClose').onclick = closeModal;
        document.getElementById('catAdd').onclick = async () => {
          const name = document.getElementById('catName').value.trim();
          if (!name) { toast('Введите название'); return; }
          try {
            await api('/finance/categories', {
              method: 'POST',
              body: {
                region_id: state.regionId,
                name: name,
                type: document.getElementById('catType').value,
                emoji: document.getElementById('catEmoji').value.trim() || null,
                keywords: document.getElementById('catWords').value.split(',').map((w) => w.trim()).filter(Boolean),
              },
            });
            closeModal();
            categoriesModal().catch(fail);
          } catch (error) { fail(error); }
        };
        on('[data-del-cat]', 'click', (event) => {
          const id = Number(event.currentTarget.dataset.delCat);
          confirmAction('Удалить категорию? Операции останутся без категории.', async () => {
            try {
              await api('/finance/categories/' + id, { method: 'DELETE' });
              categoriesModal().catch(fail);
            } catch (error) { fail(error); }
          });
        });
      });
  }

  // --- Календарь (переиспользуется «Мероприятиями» организатора и личным
  // календарём участника) ------------------------------------------------

  // Не toISOString().slice(0,10) — тот сначала переводит в UTC и в регионах
  // восточнее Гринвича (вся Россия) может откатить дату на день назад в
  // первые часы суток по местному времени. Собираем строку из локальных
  // year/month/day напрямую.
  function isoFromYMD(year, monthIndex, day) {
    const d = new Date(year, monthIndex, day); // сам нормализует переполнение (напр. day=0 → конец предыдущего месяца)
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  function todayIsoLocal() {
    const now = new Date();
    return isoFromYMD(now.getFullYear(), now.getMonth(), now.getDate());
  }

  function calendarGridHtml(events, monthDate) {
    const year = monthDate.getFullYear();
    const month = monthDate.getMonth();
    const startOffset = (new Date(year, month, 1).getDay() + 6) % 7; // понедельник = 0
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const prevMonthDays = new Date(year, month, 0).getDate();

    // В клетке видны и мероприятия, и сроки задач: человек смотрит в календарь,
    // чтобы понять, что его ждёт, а срок задачи ждёт его не меньше встречи.
    // Различаются цветом точки, разбираются по строкам в окне дня.
    const eventsByDate = {};
    events.forEach((e) => { (eventsByDate[e.date] = eventsByDate[e.date] || []).push(e); });

    const cells = [];
    for (let i = startOffset - 1; i >= 0; i--) cells.push({ day: prevMonthDays - i, m: month - 1, outside: true });
    for (let d = 1; d <= daysInMonth; d++) cells.push({ day: d, m: month, outside: false });
    let nextDay = 1;
    while (cells.length % 7 !== 0) cells.push({ day: nextDay++, m: month + 1, outside: true });

    const todayIso = todayIsoLocal();
    const weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
    const monthLabel = monthDate.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });

    const dayCells = cells.map((cell) => {
      const iso = isoFromYMD(year, cell.m, cell.day);
      const dayEvents = eventsByDate[iso] || [];
      const classes = ['calendar__day'];
      if (cell.outside) classes.push('calendar__day--outside');
      if (iso === todayIso) classes.push('calendar__day--today');
      const dots = dayEvents.length
        ? '<div class="calendar__day-dots">' + dayEvents.slice(0, 4).map((item) =>
            '<span class="calendar__dot' + (item.kind === 'task' ? ' calendar__dot--task' : '') + '"></span>').join('') +
          '</div>'
        : '';
      return '<div class="' + classes.join(' ') + '" data-cal-day="' + iso + '">' +
        '<div class="calendar__day-num">' + cell.day + '</div>' + dots + '</div>';
    }).join('');

    return '<div class="calendar__nav"><button type="button" data-cal-nav="-1">‹</button>' +
      '<div class="calendar__nav-title">' + esc(monthLabel.charAt(0).toUpperCase() + monthLabel.slice(1)) + '</div>' +
      '<button type="button" data-cal-nav="1">›</button></div>' +
      '<div class="calendar__grid">' + weekdays.map((w) => '<div class="calendar__weekday">' + w + '</div>').join('') + dayCells + '</div>';
  }

  // Сроки задач для календаря. Выполненные и отменённые не показываем —
  // календарь про то, что впереди, а не про то, что уже позади.
  //
  // Ящиков два, и берём мы их по-разному. В личном кабинете — только
  // входящие: там человек получает работу, и «поставленные мной» оттуда
  // убраны. В кабинете управления — ещё и поставленные им самим: руководитель
  // смотрит в календарь именно затем, чтобы увидеть, что и когда должны сдать
  // его люди. Раньше календарь везде показывал одни входящие, и у того, кто
  // задачи только раздаёт, он был пуст — сроков нет, хотя задачи есть.
  async function taskDeadlines(withOutbox) {
    const boxes = withOutbox ? ['inbox', 'outbox'] : ['inbox'];
    try {
      const results = await Promise.all(boxes.map((box) => api('/tasks?box=' + box)));
      const seen = new Set();
      const items = [];
      results.forEach((data, i) => {
        const mine = boxes[i] === 'outbox';
        data.items.forEach((t) => {
          if (!t.deadline) return;
          if (t.effective_status === 'done' || t.effective_status === 'cancelled') return;
          if (seen.has(t.id)) return; // задачу самому себе иначе покажем дважды
          seen.add(t.id);
          items.push({
            id: t.id,
            kind: 'task',
            date: t.deadline,
            title: t.title,
            // У поставленной задачи важно, с кого спрос, — иначе в списке дня
            // висит название без адресата.
            status_label: (mine && t.to_name ? t.to_name + ' · ' : '') + t.status_label,
          });
        });
      });
      return items;
    } catch (error) {
      return []; // календарь без сроков лучше, чем календарь без календаря
    }
  }

  // handlers: { onDayClick(iso, dayEvents), onMonthChange(monthDate) }
  function renderCalendarGrid(container, events, monthDate, handlers) {
    container.innerHTML = calendarGridHtml(events, monthDate);
    on('[data-cal-nav]', 'click', (event) => {
      const next = new Date(monthDate.getFullYear(), monthDate.getMonth() + Number(event.currentTarget.dataset.calNav), 1);
      if (handlers.onMonthChange) handlers.onMonthChange(next);
      renderCalendarGrid(container, events, next, handlers);
    }, container);
    on('[data-cal-day]', 'click', (event) => {
      const iso = event.currentTarget.dataset.calDay;
      const dayEvents = events.filter((e) => e.date === iso);
      handlers.onDayClick(iso, dayEvents);
    }, container);
  }

  // options: { canCreate, onOpen(eventId), onCreate(iso) }
  function dayAgendaModal(iso, dayEvents, options) {
    const rows = dayEvents.length ? dayEvents.map((item) =>
      item.kind === 'task'
        ? '<div class="row"><div class="row__main">' +
          '<div class="row__title">' + esc(item.title) + '</div>' +
          '<div class="row__sub">Срок задачи · ' + esc(item.status_label) + '</div></div></div>'
        : '<div class="row row--clickable" data-agenda-event="' + item.id + '">' +
          '<div class="row__main"><div class="row__title">' + esc(item.title) + (item.is_recurring ? ' 🔁' : '') + '</div>' +
          '<div class="row__sub">' + esc(item.status_label) + '</div></div></div>'
    ).join('') : '<div class="empty">Ничего не назначено</div>';

    modal(dateRu(iso),
      '<div class="card">' + rows + '</div>' +
      (options.canCreate ? '<button class="btn btn--block" id="agendaAdd">➕ Создать на этот день</button>' : '') +
      '<div class="btn-row"><button class="btn btn--ghost" id="agendaClose">Закрыть</button></div>',
      () => {
        document.getElementById('agendaClose').onclick = closeModal;
        on('[data-agenda-event]', 'click', (event) => {
          closeModal();
          options.onOpen(Number(event.currentTarget.dataset.agendaEvent));
        });
        if (options.canCreate) {
          document.getElementById('agendaAdd').onclick = () => { closeModal(); options.onCreate(iso); };
        }
      });
  }

  // --- Мероприятия ----------------------------------------------------------

  function renderEventRows(items) {
    return items.length ? items.map((event) => {
      let budget = '';
      if (event.planned_budget) {
        const fact = event.fact_expense || 0;
        const share = Math.min(Math.round((fact / event.planned_budget) * 100), 200);
        budget = '<div class="progress"><div class="progress__bar' + (fact > event.planned_budget ? ' progress__bar--over' : '') +
          '" style="width:' + Math.min(share, 100) + '%"></div></div>' +
          '<div class="row__sub">План ' + money(event.planned_budget) + ' · факт ' + money(fact) + '</div>';
      }
      return '<div class="row row--clickable" data-event="' + event.id + '">' +
        '<div class="row__main"><div class="row__title">' + esc(event.title) +
        (event.is_recurring ? ' 🔁' : '') + '</div>' +
        '<div class="row__sub">' + dateRu(event.date) + (event.time ? ', ' + event.time : '') + ' · ' + esc(event.status_label) +
        '</div>' + budget + '</div></div>';
    }).join('') : '<div class="empty">Мероприятий нет</div>';
  }

  async function renderEvents(gen) {
    if (needRegion(gen)) return;
    if (!state.calendarMonth) state.calendarMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    if (!state.eventsScope) state.eventsScope = 'upcoming';

    // Календарь и список показываем вместе, а не по переключателю: в клетке
    // видна только точка, а дату, время и статус — перенесено, отменено —
    // показывает список. Прежний переключатель «Календарь / Список» стал не
    // нужен, его место заняли рамки списка.
    const [calendarData, listData, deadlines] = await Promise.all([
      api('/events?region_id=' + state.regionId + '&scope=all'),
      api('/events?region_id=' + state.regionId + '&scope=' + state.eventsScope),
      taskDeadlines(true),
    ]);
    const calendarItems = calendarData.items.concat(deadlines);

    setView(
      (canEdit() ? '<button class="btn btn--block" id="addEvent">➕ Создать мероприятие</button>' : '') +
      '<div class="card" id="calRoot"></div>' +
      '<div class="chips">' +
      [['upcoming', 'Предстоящие'], ['past', 'Прошедшие'], ['all', 'Все']].map((s2) =>
        '<button class="chip' + (state.eventsScope === s2[0] ? ' chip--active' : '') +
        '" data-scope="' + s2[0] + '">' + s2[1] + '</button>').join('') +
      '</div>' +
      '<div class="card">' + renderEventRows(listData.items) + '</div>'
    , gen);

    if (canEdit()) document.getElementById('addEvent').onclick = () => eventForm(null);
    renderCalendarGrid(document.getElementById('calRoot'), calendarItems, state.calendarMonth, {
      onMonthChange: (next) => { state.calendarMonth = next; },
      onDayClick: (iso, dayEvents) => dayAgendaModal(iso, dayEvents, {
        canCreate: canEdit(),
        onOpen: (id) => eventCard(id).catch(fail),
        onCreate: (day) => eventForm(null, day),
      }),
    });
    on('[data-scope]', 'click', (event) => {
      state.eventsScope = event.currentTarget.dataset.scope;
      startRender(renderEvents);
    });
    on('[data-event]', 'click', (event) => eventCard(Number(event.currentTarget.dataset.event)).catch(fail));
  }

  // --- Мероприятия (личный кабинет участника) --------------------------------
  // Только просмотр региона + своя отметка «иду/не иду» — никакого
  // редактирования (см. api/routers/events.py::list_my_events, set_rsvp).

  // Список под календарём: в клетке видно только точку, а список показывает
  // дату, время и текущий статус мероприятия. Если мероприятие перенесли или
  // отменили, здесь это видно сразу — в отличие от новости о нём, которая
  // остаётся написанной по-старому.
  function participantEventRows(items) {
    if (!items.length) return '<div class="empty">Мероприятий нет</div>';
    return items.map((event) => {
      const mark = event.going === true ? 'иду' : event.going === false ? 'не иду' : 'нет отметки';
      return '<div class="row row--clickable" data-my-event="' + event.id + '">' +
        '<div class="row__main"><div class="row__title">' + esc(event.title) + '</div>' +
        '<div class="row__sub">' + dateRu(event.date) + (event.time ? ', ' + event.time : '') +
        ' · ' + esc(event.status_label || '') + ' · ' + mark + '</div>' +
        '<div class="row__sub">' + esc(eventOrganizerLabel(event)) + '</div></div></div>';
    }).join('');
  }

  async function renderParticipantEvents(gen) {
    if (!state.myEventsCalendarMonth) {
      state.myEventsCalendarMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    }
    if (!state.myEventsScope) state.myEventsScope = 'upcoming';
    const [data, deadlines] = await Promise.all([api('/events/mine?scope=all'), taskDeadlines()]);

    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const isPast = (event) => new Date(event.date + 'T00:00:00') < today;
    const byScope = {
      upcoming: data.items.filter((e) => !isPast(e)).sort((a, b) => a.date.localeCompare(b.date)),
      past: data.items.filter(isPast).sort((a, b) => b.date.localeCompare(a.date)),
      all: data.items.slice().sort((a, b) => b.date.localeCompare(a.date)),
    };

    setView(
      '<div class="card" id="calRootMine"></div>' +
      '<div class="chips">' +
      [['upcoming', 'Предстоящие'], ['past', 'Прошедшие'], ['all', 'Все']].map((v) =>
        '<button class="chip' + (state.myEventsScope === v[0] ? ' chip--active' : '') +
        '" data-my-scope="' + v[0] + '">' + v[1] + '</button>').join('') +
      '</div>' +
      '<div class="card">' + participantEventRows(byScope[state.myEventsScope] || []) + '</div>'
    , gen);

    renderCalendarGrid(document.getElementById('calRootMine'), data.items.concat(deadlines), state.myEventsCalendarMonth, {
      onMonthChange: (next) => { state.myEventsCalendarMonth = next; },
      onDayClick: (iso, dayEvents) => dayAgendaModal(iso, dayEvents, {
        canCreate: false,
        onOpen: (id) => {
          const event = dayEvents.find((e) => e.kind !== 'task' && e.id === id);
          if (event) participantEventCard(event);
        },
      }),
    });
    on('[data-my-scope]', 'click', (event) => {
      state.myEventsScope = event.currentTarget.dataset.myScope;
      startRender(renderParticipantEvents);
    });
    on('[data-my-event]', 'click', (event) => {
      const found = data.items.find((e) => e.id === Number(event.currentTarget.dataset.myEvent));
      if (found) participantEventCard(found);
    });
  }

  // Организатор — вузовская ячейка, если мероприятие её, иначе региональное
  // отделение целиком (event.cell_id / event.region_name, см. api/routers/
  // events.py::get_event и list_my_events).
  function eventOrganizerLabel(event) {
    return event.cell_name || event.region_name || '—';
  }

  // Телефон/telegram куратора — оба вместе, если оба указаны.
  function eventContactLabel(event) {
    return [event.responsible_phone, event.responsible_telegram].filter(Boolean).join(' · ');
  }

  function participantEventCard(event) {
    const rsvpLabel = event.going === true ? 'Иду ✅' : event.going === false ? 'Не иду ❌' : 'Ещё не отвечал(а)';
    const contact = eventContactLabel(event);
    modal(event.title,
      '<div class="card">' +
      '<div class="row"><div class="row__main">Дата</div><div class="row__side">' + dateRu(event.date) + (event.time ? ', ' + event.time : '') + '</div></div>' +
      '<div class="row"><div class="row__main">Организатор</div><div class="row__side">' + esc(eventOrganizerLabel(event)) + '</div></div>' +
      (event.responsible_name ? '<div class="row"><div class="row__main">Ответственный</div><div class="row__side">' + esc(event.responsible_name) + '</div></div>' : '') +
      (contact ? '<div class="row"><div class="row__main">Контакты</div><div class="row__side">' + esc(contact) + '</div></div>' : '') +
      '<div class="row"><div class="row__main">Моя отметка</div><div class="row__side">' + esc(rsvpLabel) + '</div></div>' +
      '</div>' +
      (event.description ? '<div class="card">' + esc(event.description) + '</div>' : '') +
      (event.status === 'planned'
        ? '<div class="btn-row"><button class="btn" id="rsvpYes">✅ Иду</button>' +
          '<button class="btn btn--ghost" id="rsvpNo">❌ Не иду</button></div>'
        : '') +
      '<div class="btn-row"><button class="btn btn--ghost" id="evClose">Закрыть</button></div>',
      () => {
        document.getElementById('evClose').onclick = closeModal;
        if (event.status !== 'planned') return;
        const rsvp = async (going) => {
          try {
            await api('/events/' + event.id + '/rsvp', { method: 'PUT', body: { going: going } });
            closeModal();
            toast(going === true ? 'Отмечено: иду' : going === false ? 'Отмечено: не иду' : 'Отметка снята');
            startRender(renderParticipantEvents);
          } catch (error) { fail(error); }
        };
        // Повторный клик по уже выбранному варианту снимает отметку.
        document.getElementById('rsvpYes').onclick = () => rsvp(event.going === true ? null : true);
        document.getElementById('rsvpNo').onclick = () => rsvp(event.going === false ? null : false);
      });
  }

  async function eventCard(eventId) {
    const event = await api('/events/' + eventId);
    const editable = canEdit();

    const budgetBlock = event.planned_budget
      ? '<div class="row"><div class="row__main">План / факт</div><div class="row__side">' +
        money(event.planned_budget) + ' / ' + money(event.fact_expense || 0) +
        '<div class="row__sub">' + (event.budget_diff >= 0 ? 'остаток ' : 'перерасход ') + money(Math.abs(event.budget_diff)) + '</div>' +
        '</div></div>'
      : '';
    const tasksDone = event.tasks.filter((t) => t.status !== 'planned').length;
    const contact = eventContactLabel(event);

    modal(event.title,
      '<div class="card">' +
      '<div class="row"><div class="row__main">Дата</div><div class="row__side">' + dateRu(event.date) + (event.time ? ', ' + event.time : '') + '</div></div>' +
      '<div class="row"><div class="row__main">Статус</div><div class="row__side">' + esc(event.status_label) + '</div></div>' +
      '<div class="row"><div class="row__main">Организатор</div><div class="row__side">' + esc(eventOrganizerLabel(event)) + '</div></div>' +
      (event.responsible_name ? '<div class="row"><div class="row__main">Ответственный</div><div class="row__side">' + esc(event.responsible_name) + '</div></div>' : '') +
      (contact ? '<div class="row"><div class="row__main">Контакты</div><div class="row__side">' + esc(contact) + '</div></div>' : '') +
      (event.is_recurring ? '<div class="row"><div class="row__main">Повтор</div><div class="row__side">' + esc(event.recurrence_rule) + '</div></div>' : '') +
      budgetBlock +
      '<div class="row"><div class="row__main">Отмечено участников</div><div class="row__side">' + event.attended_member_ids.length + ' из ' + event.members.length + '</div></div>' +
      (event.tasks.length ? '<div class="row"><div class="row__main">Задачи</div><div class="row__side">' + tasksDone + ' из ' + event.tasks.length + ' закрыто</div></div>' : '') +
      '</div>' +
      (event.description ? '<div class="card">' + esc(event.description) + '</div>' : '') +
      '<div class="btn-row">' +
      '<button class="btn" id="evTasks">📋 Задачи' + (event.tasks.length ? ' (' + tasksDone + '/' + event.tasks.length + ')' : '') + '</button>' +
      (editable ? '<button class="btn" id="evAttendance">✅ Участники</button>' : '') +
      (editable ? '<button class="btn btn--ghost" id="evEdit">✏️ Изменить</button>' : '') +
      (editable ? '<button class="btn btn--danger" id="evDelete">Удалить</button>' : '') +
      '<button class="btn btn--ghost" id="evClose">Закрыть</button></div>',
      () => {
        document.getElementById('evClose').onclick = closeModal;
        document.getElementById('evTasks').onclick = () => eventTasksModal(event, editable).catch(fail);
        if (!editable) return;
        document.getElementById('evEdit').onclick = () => eventForm(event);
        document.getElementById('evAttendance').onclick = () => attendanceModal(event);
        document.getElementById('evDelete').onclick = () => confirmAction('Удалить мероприятие?', async () => {
          try {
            await api('/events/' + event.id + (event.is_recurring ? '?with_series=true' : ''), { method: 'DELETE' });
            closeModal();
            startRender(renderEvents);
          } catch (error) { fail(error); }
        });
      });
  }

  // Подзадачи цели-мероприятия — своя дата и статус, несколько исполнителей
  // на каждую. Мероприятие само закрывается, когда закрыта последняя
  // подзадача (services/сервер — см. api/routers/event_tasks.py); руками
  // закрыть мероприятие с открытыми подзадачами нельзя (api/routers/events.py).
  async function eventTasksModal(event, editable) {
    const data = await api('/events/' + event.id + '/tasks');
    const statuses = [['planned', 'Новая'], ['in_progress', 'В работе'], ['done', 'Выполнена']];

    // Выполненные и отменённые — в конец списка, чтобы наверху всегда было
    // видно, что ещё в работе (порядок внутри каждой группы не трогаем).
    const sorted = [...data.items].sort((a, b) => (a.status === 'planned') === (b.status === 'planned') ? 0 : a.status === 'planned' ? -1 : 1);

    const rows = sorted.length ? sorted.map((t, i) =>
      '<div class="task-row"><div class="row">' +
      '<div class="row__main">' +
      '<div class="row__title">' + (i + 1) + '. ' + esc(t.title) + '</div>' +
      '<div class="row__sub">' + dateRu(t.due_date) +
      ' · ' + esc(t.assignee_name || 'без исполнителя') + '</div>' +
      '</div>' +
      (editable
        ? '<div class="row__side"><button class="btn-icon" data-task-edit="' + t.id + '" aria-label="Изменить">✏️</button>' +
          '<button class="btn-icon btn-icon--danger" data-task-delete="' + t.id + '" aria-label="Удалить">✕</button></div>'
        : '<span class="badge">' + esc(t.status_label) + '</span>') +
      '</div>' +
      (editable
        ? '<select class="task-row__status" data-task-status="' + t.id + '">' +
          statuses.map((s) => '<option value="' + s[0] + '"' + (t.status === s[0] ? ' selected' : '') + '>' + s[1] + '</option>').join('') +
          '</select>'
        : '') +
      '</div>'
    ).join('') : '<div class="empty">Задач пока нет</div>';

    modal('Задачи — ' + event.title,
      '<div class="card">' + rows + '</div>' +
      (editable ? '<button class="btn btn--block" id="etAdd">+ Добавить задачу</button>' : '') +
      '<div class="btn-row"><button class="btn btn--ghost" id="etsClose">Закрыть</button></div>',
      () => {
        document.getElementById('etsClose').onclick = closeModal;
        if (!editable) return;
        document.getElementById('etAdd').onclick = () => eventTaskForm(event, null, editable);
        on('[data-task-status]', 'change', async (ev) => {
          const taskId = Number(ev.currentTarget.dataset.taskStatus);
          try {
            await api('/events/' + event.id + '/tasks/' + taskId, { method: 'PATCH', body: { status: ev.currentTarget.value } });
            toast('Сохранено');
            eventTasksModal(event, editable).catch(fail);
          } catch (error) { fail(error); }
        });
        on('[data-task-edit]', 'click', (ev) => {
          const taskId = Number(ev.currentTarget.dataset.taskEdit);
          const task = data.items.find((t) => t.id === taskId);
          eventTaskForm(event, task, editable);
        });
        on('[data-task-delete]', 'click', (ev) => {
          const taskId = Number(ev.currentTarget.dataset.taskDelete);
          confirmAction('Удалить задачу?', async () => {
            try {
              await api('/events/' + event.id + '/tasks/' + taskId, { method: 'DELETE' });
              eventTasksModal(event, editable).catch(fail);
            } catch (error) { fail(error); }
          });
        });
      });
  }

  function eventTaskForm(event, task, editable) {
    // Один исполнитель, он же ответственный: список с галочками заменён на
    // выбор одного человека — спрашивать иначе не с кого.
    const current = task ? task.assignee_member_id : null;
    modal(task ? 'Изменить задачу' : 'Новая задача',
      '<div class="field"><label>Название</label><input id="etTitle" value="' + esc(task ? task.title : '') + '" /></div>' +
      // Дату набирают руками, как везде в кабинете: календарь на телефоне
      // рисуется системным шрифтом и вылезал за край (см. applyDateMask).
      '<div class="field"><label>Срок</label><input id="etDate" placeholder="20.02.2026" inputmode="numeric" value="' +
      esc(isoToRuDate(task ? task.due_date : todayIsoLocal())) + '" /></div>' +
      '<div class="field"><label>Исполнитель</label><select id="etAssignee">' +
      '<option value="">— не назначен</option>' +
      event.members.map((m) =>
        '<option value="' + m.id + '"' + (current === m.id ? ' selected' : '') + '>' +
        esc(m.full_name) + '</option>').join('') +
      '</select></div>' +
      '<div class="btn-row"><button class="btn" id="etSave">Сохранить</button>' +
      '<button class="btn btn--ghost" id="etCancel">Отмена</button></div>',
      () => {
        applyDateMask(document.getElementById('etDate'));
        document.getElementById('etCancel').onclick = closeModal;
        document.getElementById('etSave').onclick = async () => {
          const title = document.getElementById('etTitle').value.trim();
          if (title.length < 2) { toast('Введите название'); return; }
          const dueRaw = document.getElementById('etDate').value.trim();
          const dueDate = ruDateToIso(dueRaw);
          if (!dueDate) { toast('Укажите срок задачи — формат ДД.ММ.ГГГГ'); return; }
          const chosen = document.getElementById('etAssignee').value;
          const payload = {
            title: title,
            due_date: dueDate,
            assignee_member_id: chosen ? Number(chosen) : null,
          };
          try {
            if (task) await api('/events/' + event.id + '/tasks/' + task.id, { method: 'PATCH', body: payload });
            else await api('/events/' + event.id + '/tasks', { method: 'POST', body: payload });
            toast('Сохранено');
            eventTasksModal(event, editable).catch(fail);
          } catch (error) { fail(error); }
        };
      });
  }

  function attendanceModal(event) {
    const checked = new Set(event.attended_member_ids);
    modal('Участники · ' + event.title,
      '<div class="field"><input id="attSearch" placeholder="Поиск по ФИО" /></div>' +
      '<div class="checklist" id="attList">' +
      event.members.map((member) =>
        '<label data-name="' + esc(member.full_name.toLowerCase()) + '">' +
        '<input type="checkbox" value="' + member.id + '"' + (checked.has(member.id) ? ' checked' : '') + ' />' +
        '<span>' + esc(member.full_name) + ' <span class="badge">' + esc(member.status_label) + '</span></span></label>').join('') +
      '</div>' +
      '<div class="btn-row"><button class="btn" id="attSave">Сохранить</button>' +
      '<button class="btn btn--ghost" id="attClose">Отмена</button></div>',
      () => {
        const search = document.getElementById('attSearch');
        search.addEventListener('input', () => {
          const needle = search.value.trim().toLowerCase();
          document.querySelectorAll('#attList label').forEach((label) => {
            label.style.display = !needle || label.dataset.name.indexOf(needle) !== -1 ? 'flex' : 'none';
          });
        });
        document.getElementById('attClose').onclick = closeModal;
        document.getElementById('attSave').onclick = async () => {
          const ids = Array.from(document.querySelectorAll('#attList input:checked')).map((input) => Number(input.value));
          try {
            await api('/events/' + event.id + '/attendance', { method: 'PUT', body: { member_ids: ids } });
            closeModal();
            toast('Отмечено: ' + ids.length);
            startRender(renderEvents);
          } catch (error) { fail(error); }
        };
      });
  }

  async function eventForm(event, prefillDate) {
    const members = await api('/members?region_id=' + state.regionId);
    const cellsData = state.me.role !== 'cell_leader' ? await api('/cells?region_id=' + state.regionId) : { items: [] };
    const statuses = [['planned', 'Запланировано'], ['done', 'Проведено'], ['cancelled', 'Отменено']];
    const rules = [['', 'Без повтора'], ['weekly', 'Еженедельно'], ['biweekly', 'Раз в две недели'], ['monthly', 'Ежемесячно']];

    modal(event ? 'Изменить мероприятие' : 'Новое мероприятие',
      '<div class="field"><label>Название</label><input id="evTitle" value="' + esc(event ? event.title : '') + '" /></div>' +
      '<div class="field"><label>Дата</label><input id="evDate" type="date" value="' + esc(event ? event.date : (prefillDate || todayIsoLocal())) + '" /></div>' +
      '<div class="field"><label>Время</label><input id="evTime" type="time" value="' + esc(event && event.time ? event.time : '') + '" /></div>' +
      (cellsData.items.length ? '<div class="field"><label>Вузовская ячейка</label><select id="evCell"><option value="">— Регионально-городское —</option>' +
        cellsData.items.map((c) => '<option value="' + c.id + '"' + (event && event.cell_id === c.id ? ' selected' : '') + '>' + esc(c.name) + '</option>').join('') +
        '</select></div>' : '') +
      '<div class="field"><label>Статус</label><select id="evStatus">' +
      statuses.map((s) => '<option value="' + s[0] + '"' + (event && event.status === s[0] ? ' selected' : '') + '>' + s[1] + '</option>').join('') + '</select></div>' +
      '<div class="field"><label>Ответственный</label><select id="evResponsible"><option value="">—</option>' +
      members.items.map((m) => '<option value="' + m.id + '"' + (event && event.responsible_member_id === m.id ? ' selected' : '') + '>' + esc(m.full_name) + '</option>').join('') +
      '</select></div>' +
      '<div class="field"><label>Планируемый бюджет, ₽</label><input id="evBudget" inputmode="decimal" value="' +
      (event && event.planned_budget ? (event.planned_budget / 100) : '') + '" /></div>' +
      '<div class="field"><label>Повтор</label><select id="evRule">' +
      rules.map((r) => '<option value="' + r[0] + '"' + (event && event.recurrence_rule === r[0] ? ' selected' : '') + '>' + r[1] + '</option>').join('') + '</select></div>' +
      '<div class="field" id="evUntilField"><label>Повторять до</label><input id="evUntil" type="date" value="' + esc(event ? event.recurrence_until || '' : '') + '" /></div>' +
      '<div class="field"><label>Описание</label><textarea id="evDescription">' + esc(event ? event.description || '' : '') + '</textarea></div>' +
      '<div class="btn-row"><button class="btn" id="evSave">Сохранить</button>' +
      '<button class="btn btn--ghost" id="evCancel">Отмена</button></div>',
      () => {
        const ruleSelect = document.getElementById('evRule');
        const untilField = document.getElementById('evUntilField');
        const syncUntilVisibility = () => { untilField.style.display = ruleSelect.value ? '' : 'none'; };
        syncUntilVisibility();
        ruleSelect.addEventListener('change', syncUntilVisibility);
        document.getElementById('evCancel').onclick = closeModal;
        document.getElementById('evSave').onclick = async () => {
          const title = document.getElementById('evTitle').value.trim();
          if (title.length < 2) { toast('Введите название'); return; }
          const description = document.getElementById('evDescription').value.trim();
          if (!description) { toast('Введите описание'); return; }
          const budgetRaw = document.getElementById('evBudget').value.replace(',', '.').trim();
          const rule = document.getElementById('evRule').value;
          const responsible = document.getElementById('evResponsible').value;
          const evCellEl = document.getElementById('evCell');
          const payload = {
            title: title,
            date: document.getElementById('evDate').value,
            time: document.getElementById('evTime').value || null,
            description: description,
            responsible_member_id: responsible ? Number(responsible) : null,
            cell_id: evCellEl ? (evCellEl.value ? Number(evCellEl.value) : null) : undefined,
            status: document.getElementById('evStatus').value,
            planned_budget: budgetRaw ? Math.round(parseFloat(budgetRaw) * 100) : null,
            is_recurring: Boolean(rule),
            recurrence_rule: rule || null,
            recurrence_until: document.getElementById('evUntil').value || null,
          };
          try {
            if (event) await api('/events/' + event.id, { method: 'PATCH', body: payload });
            else await api('/events', { method: 'POST', body: Object.assign({ region_id: state.regionId }, payload) });
            closeModal();
            toast('Сохранено');
            startRender(renderEvents);
          } catch (error) { fail(error); }
        };
      });
  }

  // --- Документы ------------------------------------------------------------

  async function renderDocuments(gen) {
    if (needRegion(gen)) return;
    const data = await api('/documents?region_id=' + state.regionId);

    const rows = data.items.length ? data.items.map((doc) =>
      '<div class="row"><div class="row__main"><div class="row__title">' + esc(doc.title) + '</div>' +
      '<div class="row__sub">' + esc(doc.doc_type || 'Без типа') + ' · ' + Math.max(1, Math.round(doc.size_bytes / 1024)) + ' КБ' +
      (doc.event_title ? ' · 🎪 ' + esc(doc.event_title) : '') +
      (doc.author_name ? ' · ' + esc(doc.author_name) : '') + '</div></div>' +
      '<div class="row__side"><button class="btn btn--ghost btn--small" data-doc-download="' + doc.id + '" data-name="' + esc(doc.original_name) + '">⬇️</button>' +
      (canEdit() ? '<button class="btn btn--ghost btn--small" data-doc-delete="' + doc.id + '">✕</button>' : '') +
      '</div></div>').join('') : '<div class="empty">Документов нет</div>';

    setView(
      (canEdit() ? '<button class="btn btn--block" id="uploadDoc">⬆️ Загрузить документ</button>' : '') +
      '<div class="card">' + rows + '</div>'
    , gen);

    on('[data-doc-download]', 'click', (event) => {
      const id = event.currentTarget.dataset.docDownload;
      download('/documents/' + id + '/download', event.currentTarget.dataset.name).catch(fail);
    });
    if (canEdit()) {
      document.getElementById('uploadDoc').onclick = () => uploadModal().catch(fail);
      on('[data-doc-delete]', 'click', (event) => {
        const id = event.currentTarget.dataset.docDelete;
        confirmAction('Удалить документ?', async () => {
          try {
            await api('/documents/' + id, { method: 'DELETE' });
            startRender(renderDocuments);
          } catch (error) { fail(error); }
        });
      });
    }
  }

  async function uploadModal() {
    const events = await api('/events?region_id=' + state.regionId + '&scope=all');
    modal('Загрузка документа',
      '<div class="field"><label>Название</label><input id="docTitle" /></div>' +
      '<div class="field"><label>Тип</label><select id="docType">' +
      state.me.dictionaries.document_types.map((t) => '<option>' + esc(t) + '</option>').join('') + '</select></div>' +
      '<div class="field"><label>Мероприятие (необязательно)</label><select id="docEvent"><option value="">—</option>' +
      events.items.map((e) => '<option value="' + e.id + '">' + esc(e.title) + ' · ' + dateShort(e.date) + '</option>').join('') +
      '</select></div>' +
      '<div class="field"><label>Файл</label><input id="docFile" type="file" /></div>' +
      '<div class="btn-row"><button class="btn" id="docSave">Загрузить</button>' +
      '<button class="btn btn--ghost" id="docClose">Отмена</button></div>',
      () => {
        document.getElementById('docClose').onclick = closeModal;
        document.getElementById('docSave').onclick = async () => {
          const fileInput = document.getElementById('docFile');
          if (!fileInput.files.length) { toast('Выберите файл'); return; }
          const form = new FormData();
          form.append('region_id', state.regionId);
          form.append('title', document.getElementById('docTitle').value.trim() || fileInput.files[0].name);
          form.append('doc_type', document.getElementById('docType').value);
          const eventId = document.getElementById('docEvent').value;
          if (eventId) form.append('event_id', eventId);
          form.append('file', fileInput.files[0]);
          try {
            await api('/documents', { method: 'POST', body: form });
            closeModal();
            toast('Загружено');
            startRender(renderDocuments);
          } catch (error) { fail(error); }
        };
      });
  }

  // --- Вузовские ячейки -------------------------------------------------------

  async function renderCells(gen) {
    if (needRegion(gen)) return;
    const data = await api('/cells?region_id=' + state.regionId);

    // Раньше руководитель, счётчик состава и ближайшее мероприятие слипались
    // в одну серую строку через «·» — взгляду не за что зацепиться. Бюджета
    // тут больше нет вовсе: денег на ячейки не расписывают.
    const rows = data.items.length ? data.items.map((cell) =>
      '<button type="button" class="card cell" data-cell="' + cell.id + '">' +
      '<span class="cell__head"><span class="cell__name">' + esc(cell.name) + '</span>' +
      '<span class="row__chev">›</span></span>' +

      '<span class="cell__line">' +
      '<span class="cell__chip' + (cell.leader_name ? '' : ' cell__chip--empty') + '">' +
      esc(cell.leader_name ? cell.leader_name.trim()[0].toUpperCase() : '—') + '</span>' +
      '<span class="' + (cell.leader_name ? '' : 'row__sub') + '">' +
      esc(cell.leader_name || 'Руководитель не назначен') + '</span></span>' +

      '<span class="cell__line row__sub">' + iconPeople() + declChelovek(cell.members_total) + ' в составе</span>' +

      (cell.next_event
        ? '<span class="cell__line row__sub">' + iconCalendar() +
          esc(cell.next_event.title) + ' — ' + dateRu(cell.next_event.date) + '</span>'
        : '') +
      '</button>').join('') : '<div class="empty">Вузовских ячеек в этом отделении нет</div>';

    setView(rows, gen);

    on('[data-cell]', 'click', (event) => {
      cellDetail(Number(event.currentTarget.dataset.cell)).catch(fail);
    });
  }

  function iconPeople() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" class="cell__icon">' +
      '<circle cx="9" cy="8" r="3.2"></circle><path d="M3 20a6 6 0 0 1 12 0M16 5.2a3.2 3.2 0 0 1 0 5.6M18 20a6 6 0 0 0-3-5.2"></path></svg>';
  }

  function iconCalendar() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" class="cell__icon">' +
      '<rect x="3" y="5" width="18" height="16" rx="2"></rect><path d="M3 10h18M8 3v4M16 3v4"></path></svg>';
  }

  function declChelovek(n) {
    const tail = n % 100 >= 11 && n % 100 <= 14 ? 0 : n % 10;
    return n + (tail === 1 ? ' человек' : (tail >= 2 && tail <= 4 ? ' человека' : ' человек'));
  }

  // Обзор, состав, мероприятия и правка лежали в одном окне подряд — на
  // телефоне это бесконечная прокрутка внутри шторки, где не найти нужное.
  // Теперь вкладки, а правка спрятана за кнопку: её открывают редко, а места
  // она занимала больше всего.
  async function cellDetail(cellId) {
    const cell = await api('/cells/' + cellId);
    const editable = canEdit();
    // Назначение руководителя ячейки — только настоящему руководителю
    // отделения (и superuser), не руководителю самой ячейки, даже если тому
    // формально можно править название (см. api/routers/cells.py).
    const canAssignCellLeader = state.me.role === 'leader' || state.me.role === 'superuser';
    let tab = 'members';
    let editing = false;

    const head = () =>
      '<div class="card duty">' +
      '<span class="duty__icon">' + iconLeader() + '</span>' +
      '<span class="duty__body"><span class="duty__label">Руководитель ячейки</span>' +
      '<span class="duty__name' + (cell.leader_name ? '' : ' duty__name--empty') + '">' +
      esc(cell.leader_name || 'не назначен') + '</span></span>' +
      (canAssignCellLeader
        ? '<span class="duty__acts">' +
          '<button type="button" class="duty__act' + (cell.leader_name ? '' : ' duty__act--go') +
          '" id="cellAssignLeader">' + (cell.leader_name ? 'Сменить' : 'Назначить') + '</button>' +
          // Снять, а не только сменить: если человек выпустился, а заменить его
          // некем, ячейка оставалась с бывшим во главе навсегда.
          (cell.leader_name
            ? '<button type="button" class="duty__act duty__act--drop" id="cellDropLeader">Снять</button>'
            : '') +
          '</span>'
        : '') +
      '</div>';

    // Три раздела, и все три — то, ради чего ячейку открывают. «Обзора» не
    // стало: он пересказывал название окна (вуз, от которого ячейка и
    // произошла) и считал мероприятия, список которых лежит рядом.
    const tabsHtml = () =>
      '<div class="chips cell__tabs">' +
      [['members', 'Состав · ' + cell.members_total], ['events', 'Мероприятия'], ['links', 'Соцсети']]
        .map(([id, label]) =>
          '<button type="button" class="chip' + (tab === id ? ' chip--active' : '') + '" data-ctab="' + id + '">' +
          esc(label) + '</button>').join('') +
      '</div>';

    // Ссылку правят прямо в её строке. Отдельной «Правки ячейки» больше нет:
    // она заводила целый экран ради одного поля — название ячейке даёт вуз,
    // а состав и мероприятия собираются сами.
    const linksHtml = () =>
      '<div class="card card--rows">' +
      '<div class="row"><div class="row__main"><div class="row__title">ВКонтакте</div>' +
      '<div class="row__sub">' + (cell.vk_url ? esc(cell.vk_url) : 'страница не указана') + '</div></div>' +
      '<div class="row__side link-acts">' +
      (cell.vk_url
        ? '<a href="' + esc(cell.vk_url) + '" target="_blank" rel="noopener">открыть</a>'
        : '') +
      (editable
        ? '<button type="button" class="duty__act" id="cellVkEdit">' +
          (cell.vk_url ? 'Изменить' : 'Указать') + '</button>'
        : '') +
      '</div></div></div>';

    const vkForm = () =>
      '<div class="field"><label>Страница ВКонтакте</label>' +
      '<input id="cellVk" placeholder="https://vk.com/…" value="' + esc(cell.vk_url || '') + '" /></div>' +
      '<div class="btn-row"><button class="btn" id="cellSave">Сохранить</button>' +
      '<button class="btn btn--ghost" id="cellEditBack">Отмена</button></div>';

    const membersHtml = () => cell.members.length
      ? '<div class="card card--rows">' + cell.members.map((m) =>
          '<div class="row"><div class="row__main"><div class="row__title">' + esc(m.full_name) + '</div>' +
          (m.status_label ? '<div class="row__sub">' + esc(m.status_label) + '</div>' : '') +
          '</div></div>').join('') + '</div>'
      : '<div class="empty">Состав пуст</div>';

    const eventsHtml = () => cell.events.length
      ? '<div class="card card--rows">' + cell.events.map((e) =>
          '<div class="row"><div class="row__main"><div class="row__title">' + esc(e.title) + '</div>' +
          '<div class="row__sub">' + dateRu(e.date) + (e.time ? ', ' + e.time : '') + '</div></div></div>').join('') +
        '</div>'
      : '<div class="empty">Мероприятий нет</div>';

    const draw = () => {
      const body = document.getElementById('cellBody');
      if (!body) return;
      body.innerHTML = head() + tabsHtml() +
        (tab === 'members' ? membersHtml()
          : tab === 'events' ? eventsHtml()
            : editing ? vkForm() : linksHtml());
      wire();
    };

    const wire = () => {
      on('[data-ctab]', 'click', (event) => { tab = event.currentTarget.dataset.ctab; editing = false; draw(); });

      const editLink = document.getElementById('cellVkEdit');
      if (editLink) editLink.onclick = () => { editing = true; draw(); };

      const back = document.getElementById('cellEditBack');
      if (back) back.onclick = () => { editing = false; draw(); };

      // Без раннего выхода: шапка с должностью рисуется и в режиме правки,
      // и её кнопкам тоже нужны обработчики.
      const save = document.getElementById('cellSave');
      if (save) submitOnce(save, 'Сохраняю…', async () => {
        const url = document.getElementById('cellVk').value.trim() || null;
        try {
          await api('/cells/' + cellId, { method: 'PATCH', body: { vk_url: url } });
          cell.vk_url = url;
          editing = false;
          toast('Сохранено');
          draw();
          startRender(renderCells);
        } catch (error) { fail(error); }
      });
      const assign = document.getElementById('cellAssignLeader');
      // Руководителя ячейки берём из её же состава, а не ищем по всему
      // отделению: человек из другого вуза руководить этой ячейкой не может,
      // а поиск по фамилии позволял его выбрать и упереться в отказ сервера.
      if (assign) assign.onclick = () => cellLeaderPicker(cell, () => {
        closeModal();
        startRender(renderCells);
      });

      const drop = document.getElementById('cellDropLeader');
      if (drop) drop.onclick = () => confirmAction({
        title: 'Снять руководителя?',
        body: cell.leader_name + ' — ячейка «' + cell.name + '» останется без руководителя.',
        confirmLabel: 'Снять',
      }, async () => {
        try {
          await api('/cells/' + cellId + '/leader', { method: 'DELETE' });
          toast('Руководитель снят');
          closeModal();
          startRender(renderCells);
        } catch (error) { fail(error); }
      });
    };

    modal(cell.name, '<div id="cellBody" class="stack"></div>', draw);
  }

  // Состав ячейки — готовый и короткий список: выбирать не из кого, кроме
  // тех, кто в этом вузе. Поэтому не поиск по фамилии, а перечень.
  function cellLeaderPicker(cell, onDone) {
    const people = (cell.members || []).filter((m) => m.full_name !== cell.leader_name);
    modal('Руководитель ячейки',
      '<div class="row__sub">' + esc(cell.name) + '</div>' +
      (people.length
        ? '<div class="card card--rows">' + people.map((m) =>
            // Кто без аккаунта — строкой видно и нажать нельзя: назначение это
            // повышение существующего аккаунта, а не создание нового. Раньше
            // отказ приходил уже после нажатия, и было непонятно, при чём тут
            // выбранный человек.
            '<button type="button" class="row' + (m.has_account ? ' row--clickable' : ' row--off') + '"' +
            (m.has_account ? ' data-cell-leader="' + m.id + '"' : ' disabled') + '>' +
            '<span class="row__main"><span class="row__title">' + esc(m.full_name) + '</span>' +
            '<span class="row__sub">' +
            (m.has_account
              ? esc(m.status_label || '')
              : 'ещё не зарегистрировался — назначить нельзя') +
            '</span></span></button>').join('') + '</div>'
        // Пустой состав — не «никого не нашлось», а «сначала заведите людей».
        : '<div class="empty">В составе ячейки пока никого нет. Человек попадает сюда, когда ему вписывают этот вуз.</div>') +
      '<div class="btn-row"><button class="btn btn--ghost" id="cellLeaderCancel">Отмена</button></div>',
      () => {
        document.getElementById('cellLeaderCancel').onclick = closeModal;
        on('[data-cell-leader]', 'click', async (event) => {
          const memberId = Number(event.currentTarget.dataset.cellLeader);
          try {
            await api('/cells/' + cell.id + '/leader', { method: 'POST', body: { member_id: memberId } });
            toast('Руководитель ячейки назначен');
            onDone();
          } catch (error) { fail(error); }
        });
      });
  }

  // Из какого кабинета смотрят «Задачи». В личном это почтовый ящик: работу
  // там получают, а не раздают. Сервер решает то же самое сам
  // (api/routers/tasks.py), это лишь говорит ему, откуда пришли.
  function tasksScope() {
    return state.cabinetMode === 'personal' ? 'personal=true' : '';
  }

  async function renderTasks(gen) {
    // В личном кабинете исходящих нет — и переключателя тоже. Если человек
    // ушёл сюда из управления, оставив «Поставленные мной», он оказался бы в
    // пустом ящике без кнопки вернуться.
    if (state.cabinetMode === 'personal') state.tasksBox = 'inbox';
    const data = await api('/tasks?box=' + state.tasksBox + '&' + tasksScope());

    // Задача мероприятия и обычная лежат в одном ящике: для человека это
    // одинаково работа. Отличаются подписью — у первой вместо «от кого»
    // стоит название мероприятия.
    const rows = data.items.length ? data.items.map((task) =>
      '<div class="row row--clickable" data-task="' + task.id + '">' +
      '<div class="row__main"><div class="row__title">' + esc(task.title) + '</div>' +
      '<div class="row__sub">' +
      esc(task.kind === 'event'
        ? task.from_name || 'мероприятие'
        : (state.tasksBox === 'inbox' ? 'от ' + (task.from_name || '') : 'кому: ' + (task.to_name || ''))) +
      (task.deadline ? ' · <span class="nowrap">до ' + dateNum(task.deadline) + '</span>' : '') + '</div></div>' +
      '<div class="row__side row__side--tight"><span class="badge badge--' + task.effective_status + '">' + esc(task.status_label) + '</span></div>' +
      '</div>').join('') : '<div class="empty">Задач нет</div>';

    setView(
      // «Поставленные мной» — только там, где их можно поставить. В личном
      // кабинете этот ящик всегда пуст, и у корпоранта он выглядел загадкой.
      (data.can_assign
        ? '<div class="chips">' +
          '<button class="chip' + (state.tasksBox === 'inbox' ? ' chip--active' : '') + '" data-box="inbox">Мои задачи</button>' +
          '<button class="chip' + (state.tasksBox === 'outbox' ? ' chip--active' : '') + '" data-box="outbox">Поставленные мной</button>' +
          '</div>'
        : '') +
      (data.can_assign ? '<button class="btn btn--block" id="addTask">➕ Поставить задачу</button>' : '') +
      '<div class="card">' + rows + '</div>'
    , gen);

    on('[data-box]', 'click', (event) => {
      state.tasksBox = event.currentTarget.dataset.box;
      startRender(renderTasks);
    });
    const add = document.getElementById('addTask');
    if (add) add.onclick = () => taskForm().catch(fail);
    on('[data-task]', 'click', (event) => {
      const task = data.items.find((t) => String(t.id) === event.currentTarget.dataset.task);
      if (task) taskCard(task);
    });
  }

  function taskCard(task) {
    const isEvent = task.kind === 'event';
    const isAssignee = isEvent || task.to_user_id === state.me.id;
    const isAuthor = !isEvent && task.from_user_id === state.me.id;
    const open = task.status === 'new' || task.status === 'in_progress';

    const actions = [];
    if (isEvent) {
      // Те же три состояния, что и у обычной задачи: человек берётся, потом
      // отчитывается. Ошибочное нажатие исправляет он же — иначе снова
      // оказывался бы ни при чём.
      if (task.status === 'planned') actions.push(['in_progress', 'Взять в работу', 'btn']);
      if (task.status !== 'done') actions.push(['done', 'Выполнена', 'btn']);
      if (task.status === 'done') actions.push(['planned', 'Вернуть', 'btn btn--ghost']);
    } else {
      if (isAssignee && task.status === 'new') actions.push(['in_progress', 'Взять в работу', 'btn']);
      if (isAssignee && open) actions.push(['done', 'Выполнена', 'btn']);
      // Снятая задача исчезает, а не оседает строкой «Отменена» в чужом ящике.
      if (isAuthor && open) actions.push(['delete', 'Снять задачу', 'btn btn--danger']);
    }

    modal(task.title,
      '<div class="card">' +
      '<div class="row"><div class="row__main">Статус</div><div class="row__side row__side--tight"><span class="badge badge--' +
      task.effective_status + '">' + esc(task.status_label) + '</span></div></div>' +
      '<div class="row"><div class="row__main">' + (isEvent ? 'Мероприятие' : 'Постановщик') + '</div>' +
      '<div class="row__side">' + esc(task.from_name || '—') + '</div></div>' +
      (isEvent ? '' :
        '<div class="row"><div class="row__main">Исполнитель</div><div class="row__side">' + esc(task.to_name || '—') + '</div></div>') +
      (task.deadline ? '<div class="row"><div class="row__main">Срок</div><div class="row__side">' + dateRu(task.deadline) + '</div></div>' : '') +
      '</div>' +
      (task.text ? '<div class="card">' + esc(task.text) + '</div>' : '') +
      '<div class="btn-row">' +
      actions.map((a) => '<button class="' + a[2] + '" data-status="' + a[0] + '">' + a[1] + '</button>').join('') +
      '<button class="btn btn--ghost" id="taskClose">Закрыть</button></div>',
      () => {
        document.getElementById('taskClose').onclick = closeModal;
        on('[data-status]', 'click', async (event) => {
          const action = event.currentTarget.dataset.status;
          // Снятие — не статус, а удаление: спрашиваем, потому что вернуть
          // задачу будет нечем, её больше нет.
          if (action === 'delete') {
            confirmAction({
              title: 'Снять задачу?',
              body: 'Задача «' + task.title + '» исчезнет и у исполнителя тоже.',
              note: 'Вернуть её будет нельзя — поставьте заново.',
              confirmLabel: 'Снять',
            }, async () => {
              try {
                await api('/tasks/' + task.id, { method: 'DELETE' });
                closeModal();
                toast('Задача снята');
                await refreshMe();
                startRender(renderTasks);
              } catch (error) { fail(error); }
            });
            return;
          }
          try {
            const path = isEvent
              ? '/tasks/event/' + String(task.id).replace('event-', '') + '/status'
              : '/tasks/' + task.id + '/status';
            await api(path, { method: 'POST', body: { status: action } });
            closeModal();
            await refreshMe();
            startRender(renderTasks);
          } catch (error) { fail(error); }
        });
      });
    // Открыли — кружок гаснет. У задачи мероприятия свой адрес: отметка о
    // прочтении лежит рядом с назначением, а не в самой задаче.
    const readPath = isEvent
      ? '/tasks/event/' + String(task.id).replace('event-', '') + '/read'
      : '/tasks/' + task.id + '/read';
    api(readPath, { method: 'POST' }).then(refreshMe).catch(() => {});
  }

  async function taskForm() {
    const people = await api('/tasks/assignees?' + tasksScope());
    if (!people.items.length) { toast('Нет доступных исполнителей'); return; }

    modal('Новая задача',
      '<div class="field"><label>Исполнитель</label><select id="tAssignee">' +
      people.items.map((p) => '<option value="' + p.id + '">' + esc(p.full_name) + (p.role_label ? ' · ' + esc(p.role_label) : '') + '</option>').join('') +
      '</select></div>' +
      '<div class="field"><label>Название</label><input id="tTitle" /></div>' +
      '<div class="field"><label>Описание</label><textarea id="tText"></textarea></div>' +
      '<div class="field"><label>Срок</label><input id="tDeadline" placeholder="20.02.2026" inputmode="numeric" /></div>' +
      '<div class="btn-row"><button class="btn" id="tSave">Поставить</button>' +
      '<button class="btn btn--ghost" id="tCancel">Отмена</button></div>',
      () => {
        applyDateMask(document.getElementById('tDeadline'));
        document.getElementById('tCancel').onclick = closeModal;
        document.getElementById('tSave').onclick = async () => {
          const title = document.getElementById('tTitle').value.trim();
          if (title.length < 2) { toast('Введите название задачи'); return; }
          // Срок необязателен, но если написан — то по-человечески.
          const deadlineRaw = document.getElementById('tDeadline').value.trim();
          const deadline = deadlineRaw ? ruDateToIso(deadlineRaw) : null;
          if (deadlineRaw && !deadline) { toast('Проверьте срок — формат ДД.ММ.ГГГГ'); return; }
          try {
            await api('/tasks?' + tasksScope(), {
              method: 'POST',
              body: {
                to_user_id: Number(document.getElementById('tAssignee').value),
                title: title,
                text: document.getElementById('tText').value.trim() || null,
                deadline: deadline,
              },
            });
            closeModal();
            toast('Задача поставлена');
            startRender(renderTasks);
          } catch (error) { fail(error); }
        };
      });
  }

  // --- Аналитика ------------------------------------------------------------

  async function renderAnalytics(gen) {
    const period = state.analyticsPeriod;
    const data = await api('/analytics/regions?period=' + period.kind + '&offset=' + period.offset);

    const head = '<tr><th>Регион</th><th class="num">Состав</th><th class="num">Новых</th><th class="num">Баланс</th>' +
      '<th class="num">Доходы</th><th class="num">Расходы</th><th class="num">Мероприятий</th></tr>';
    const body = data.items.map((row) =>
      '<tr data-region="' + row.region_id + '"><td>' + esc(row.region) +
      '<div class="row__sub">' + esc(row.leader || '—') + '</div></td>' +
      '<td class="num">' + row.members_total + '</td>' +
      '<td class="num">' + row.new_members + '</td>' +
      '<td class="num">' + money(row.balance) + '</td>' +
      '<td class="num">' + money(row.income) + '</td>' +
      '<td class="num">' + money(row.expense) + '</td>' +
      '<td class="num">' + row.events_done + ' / ' + row.events_total + '</td></tr>').join('');

    setView(
      periodControls(period, 'an') +
      '<div class="grid">' +
      '<div class="stat"><div class="stat__label">Регионов</div><div class="stat__value">' + data.items.length + '</div></div>' +
      '<div class="stat"><div class="stat__label">Всего людей</div><div class="stat__value">' + (data.totals.members || 0) + '</div></div>' +
      '<div class="stat"><div class="stat__label">Новых · ' + esc(data.period_label) + '</div><div class="stat__value">' + (data.totals.new_members || 0) + '</div></div>' +
      '<div class="stat"><div class="stat__label">Суммарный баланс</div><div class="stat__value">' + money(data.totals.balance || 0) + '</div></div>' +
      '<div class="stat"><div class="stat__label">Мероприятий · ' + esc(data.period_label) + '</div><div class="stat__value">' + (data.totals.events || 0) + '</div></div>' +
      '</div>' +
      '<div class="card"><div class="card__title">Сравнение регионов · ' + esc(data.period_label) + '</div>' +
      '<div class="table-scroll"><table><thead>' + head + '</thead><tbody>' + body + '</tbody></table></div></div>'
    , gen);

    bindPeriod(period, 'an', () => startRender(renderAnalytics));
    on('tr[data-region]', 'click', (event) => {
      state.cabinetMode = 'region';
      state.regionId = Number(event.currentTarget.dataset.region);
      state.tab = 'dashboard';
      renderCabinetSwitch();
      renderTabs();
      renderTab();
    });
  }

  // --- Отчёты ---------------------------------------------------------------

  async function renderReports(gen) {
    if (needRegion(gen)) return;
    const regionName = (state.me.regions.find((r) => r.id === state.regionId) || {}).name || '';

    setView(
      '<div class="card"><div class="card__title">Отчёт по региону: ' + esc(regionName) + '</div>' +
      '<p class="row__sub">Полный отчёт — финансы, состав и мероприятия — одним файлом (ТЗ §13).</p>' +
      '<div class="field"><label>Период</label><select id="rpPeriod">' +
      '<option value="month">Текущий месяц</option>' +
      '<option value="semester">Текущий семестр</option>' +
      '<option value="year" selected>Текущий год</option>' +
      '<option value="all">За всё время</option></select></div>' +
      '<div class="btn-row"><button class="btn" id="rpXlsx">⬇️ Excel (.xlsx)</button>' +
      '<button class="btn btn--ghost" id="rpPdf">⬇️ PDF</button></div></div>'
    , gen);

    const build = (format) => {
      const period = document.getElementById('rpPeriod').value;
      const url = '/reports/region?region_id=' + state.regionId + '&format=' + format + '&period=' + period;
      toast('Формирую отчёт…');
      download(url, 'report_' + state.regionId + '.' + format).catch(fail);
    };
    document.getElementById('rpXlsx').onclick = () => build('xlsx');
    document.getElementById('rpPdf').onclick = () => build('pdf');
  }

  // --- Регионы (создание, назначение руководителя/координатора) -------------
  // Видна federal/coordinator/superuser — замена бывших кнопок бота
  // (handlers/create_account.py, удалён), см. api/routers/regions.py.
  // Назначение руководителя вузовской ячейки — не здесь, а во вкладке
  // «Вузовские ячейки» (см. cellDetail ниже) — так руководитель региона
  // работает с этим прямо там, где смотрит свои ячейки.

  function personPicker(regionId, onPick) {
    // picked — защита от двойного клика/тапа: без неё onPick мог уйти дважды
    // до того, как первый запрос успевал закрыть модалку (два одинаковых
    // уведомления при назначении на должность).
    let picked = false;
    modal('Выберите человека из состава',
      '<div class="field"><input id="apSearch" placeholder="Начните вводить ФИО" autocomplete="off" /></div>' +
      '<div id="apResults" class="card"></div>' +
      '<div class="btn-row"><button class="btn btn--ghost" id="apClose">Отмена</button></div>',
      () => {
        document.getElementById('apClose').onclick = closeModal;
        const input = document.getElementById('apSearch');
        const results = document.getElementById('apResults');
        let timer;
        input.addEventListener('input', () => {
          clearTimeout(timer);
          const q = input.value.trim();
          if (q.length < 2) { results.innerHTML = ''; return; }
          timer = setTimeout(async () => {
            try {
              const params = new URLSearchParams({ q: q });
              if (regionId) params.set('region_id', regionId);
              const data = await api('/members/search?' + params.toString());
              results.innerHTML = data.items.length
                ? data.items.map((m) =>
                    '<div class="row row--clickable" data-pick="' + m.id + '"><div class="row__main"><div class="row__title">' +
                    esc(m.full_name) + '</div>' +
                    '</div></div>'
                  ).join('')
                : '<div class="empty">Совпадений нет</div>';
              on('[data-pick]', 'click', (event) => {
                if (picked) return;
                picked = true;
                onPick(Number(event.currentTarget.dataset.pick));
              }, results);
            } catch (error) { /* автодополнение необязательно */ }
          }, 300);
        });
      });
  }

  function regionsCreateForm() {
    // Без родительного падежа — подпись «Академисты | Название» строится
    // из одного и того же названия везде (utils/roles.py::region_display_name),
    // отдельно склонять регион при создании больше не нужно.
    modal('Новый регион',
      '<div class="field"><label>Название</label><input id="arName" placeholder="Новосибирск" /></div>' +
      '<div class="btn-row"><button class="btn" id="arSave">Создать</button><button class="btn btn--ghost" id="arClose">Отмена</button></div>',
      () => {
        document.getElementById('arClose').onclick = closeModal;
        document.getElementById('arSave').onclick = async () => {
          const name = document.getElementById('arName').value.trim();
          if (name.length < 2) { toast('Введите название'); return; }
          try {
            await api('/regions', { method: 'POST', body: { name: name } });
            closeModal();
            toast('Регион создан');
            startRender(renderRegionsTab);
          } catch (error) { fail(error); }
        };
      });
  }

  // Два шага вместо одного. Прежний порядок был обратный: сначала галочки
  // отделений, потом поиск человека — и щелчок по фамилии сразу всё применял.
  // Не отметил отделения — узнавал об этом уже после нажатия, а шага
  // «проверьте и подтвердите» не было вовсе.
  function coordinatorForm(regions, preselectRegionId) {
    let person = null;
    let picked = preselectRegionId ? [preselectRegionId] : [];

    const draw = () => {
      const body = document.getElementById('coordBody');
      if (!body) return;
      body.innerHTML = person ? stepRegions() : stepPerson();
      wire();
    };

    const stepPerson = () =>
      '<div class="field"><input id="coordSearch" placeholder="ФИО человека из состава" autocomplete="off" /></div>' +
      '<div id="coordResults"></div>';

    const stepRegions = () => {
      const chosen = regions.filter((r) => picked.includes(r.id));
      const taken = chosen.filter((r) => r.coordinator_user_id);
      return '<div class="card duty">' +
        avatarHtml(null, person.full_name) +
        '<span class="duty__body" style="margin-left:var(--space-8)">' +
        '<span class="duty__label">Куратор</span>' +
        '<span class="duty__name">' + esc(person.full_name) + '</span></span>' +
        '<span class="duty__acts"><button type="button" class="duty__act" id="coordBack">Другой</button></span></div>' +

        '<div class="section-title">Какие отделения ведёт</div>' +
        // Видимая галочка, а не спрятанная: раньше здесь стоял .row--switch,
        // который гасит сам чекбокс и рисует вместо него тумблер, — а тумблера
        // не было, и выбранное от невыбранного не отличалось ничем.
        '<div class="card card--rows">' +
        regions.map((r) =>
          '<label class="row row--pick' + (picked.includes(r.id) ? ' row--pick-on' : '') + '">' +
          '<input type="checkbox" class="check" value="' + r.id + '"' + (picked.includes(r.id) ? ' checked' : '') + ' />' +
          '<span class="pick__box">' + iconTick() + '</span>' +
          '<span class="row__main">' +
          '<span class="row__title">' + esc(r.name) + '</span>' +
          '<span class="row__sub">' +
          (r.coordinator_name ? 'сейчас ведёт ' + esc(r.coordinator_name) : 'куратора нет') +
          '</span></span>' +
          '</label>').join('') +
        '</div>' +

        // Итог словами до нажатия, а не после.
        '<div class="notice' + (picked.length ? '' : ' notice--warn') + '" id="coordSummary">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 8v5M12 16v.01"></path></svg>' +
        '<div>' + esc(
          !picked.length
            ? 'Отметьте хотя бы одно отделение — без этого назначать нечего.'
            : 'Возьмёт ' + declOtdelenie(picked.length) + ': ' + chosen.map((r) => r.name).join(', ') + '.' +
              (taken.length
                ? ' У ' + taken.map((r) => r.name).join(', ') + ' сменится куратор — ' +
                  taken.map((r) => r.coordinator_name).join(', ') + ' его потеряет.'
                : '')
        ) + '</div></div>' +

        '<div class="btn-row">' +
        '<button class="btn" id="coordSave"' + (picked.length ? '' : ' disabled') + '>Назначить</button>' +
        '<button class="btn btn--ghost" id="coordCancel">Отмена</button></div>';
    };

    const wire = () => {
      const search = document.getElementById('coordSearch');
      if (search) {
        const results = document.getElementById('coordResults');
        let timer;
        search.addEventListener('input', () => {
          clearTimeout(timer);
          const q = search.value.trim();
          if (q.length < 2) { results.innerHTML = ''; return; }
          timer = setTimeout(async () => {
            try {
              const found = await api('/members/search?q=' + encodeURIComponent(q));
              // Человека без личного кабинета назначить нельзя — сервер
              // откажет (services/admin_actions.py). Раньше об этом узнавали
              // только после нажатия: строка выглядела обычной.
              results.innerHTML = found.items.length
                ? '<div class="card card--rows">' + found.items.map((m) =>
                    '<div class="row' + (m.has_account ? ' row--clickable' : ' row--muted') + '"' +
                    (m.has_account ? ' data-pick="' + m.id + '"' : '') + '>' +
                    '<div class="row__main"><div class="row__title">' + esc(m.full_name) + '</div>' +
                    (m.has_account ? '' : '<div class="row__sub">нет личного кабинета — сначала регистрация</div>') +
                    '</div></div>').join('') +
                  '</div>'
                : '<div class="empty">Совпадений нет</div>';
              on('[data-pick]', 'click', (event) => {
                const row = found.items.find((m) => String(m.id) === event.currentTarget.dataset.pick);
                person = row;
                draw();
              }, results);
            } catch (error) { /* автодополнение необязательно */ }
          }, 300);
        });
        search.focus();
        return;
      }

      document.getElementById('coordBack').onclick = () => { person = null; draw(); };
      document.getElementById('coordCancel').onclick = closeModal;
      on('.check', 'change', (event) => {
        const id = Number(event.currentTarget.value);
        picked = event.currentTarget.checked ? picked.concat([id]) : picked.filter((x) => x !== id);
        draw();
      });
      submitOnce(document.getElementById('coordSave'), 'Назначаю…', async () => {
        try {
          await api('/regions/coordinators', {
            method: 'POST',
            body: { member_id: person.id, region_ids: picked },
          });
          closeModal();
          toast('Куратор назначен');
          startRender(renderRegionsTab);
        } catch (error) { fail(error); }
      });
    };

    modal('Назначить куратора', '<div id="coordBody" class="stack"></div>', draw);
  }

  function iconTick() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" ' +
      'stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"></path></svg>';
  }

  function declOtdelenie(n) {
    const tail = n % 100 >= 11 && n % 100 <= 14 ? 0 : n % 10;
    return n + (tail === 1 ? ' отделение' : (tail >= 2 && tail <= 4 ? ' отделения' : ' отделений'));
  }

  function regionEditForm(region) {
    modal(region.name,
      '<div class="field"><label>Название</label><input id="reName" value="' + esc(region.name) + '" /></div>' +
      '<div class="btn-row"><button class="btn" id="reSave">Сохранить</button>' +
      '<button class="btn btn--ghost" id="reClose">Закрыть</button></div>' +
      '<div class="btn-row" style="margin-top:var(--space-16)"><button class="btn btn--danger" id="reDelete">Архивировать регион</button></div>',
      () => {
        document.getElementById('reClose').onclick = closeModal;
        document.getElementById('reSave').onclick = async () => {
          const name = document.getElementById('reName').value.trim();
          if (name.length < 2) { toast('Введите название'); return; }
          try {
            await api('/regions/' + region.id, { method: 'PATCH', body: { name: name } });
            closeModal();
            toast('Сохранено');
            startRender(renderRegionsTab);
          } catch (error) { fail(error); }
        };
        document.getElementById('reDelete').onclick = () => confirmAction(
          'Архивировать регион «' + region.name + '»? Он исчезнет из рабочих списков, ' +
          'ссылка регистрации перестанет работать, а данные сохранятся для восстановления.',
          async () => {
            try {
              await api('/regions/' + region.id, { method: 'DELETE' });
              closeModal();
              toast('Регион архивирован');
              startRender(renderRegionsTab);
            } catch (error) { fail(error); }
          });
      });
  }

  async function renderRegionsTab(gen) {
    const data = await api('/regions');

    // Должность — своей строкой со своим действием. Раньше руководителя можно
    // было только сменить, а просто снять — нет; у координатора всё наоборот:
    // снять из строки можно, а назначить — только отдельной кнопкой внизу
    // экрана, оторванной от того региона, о котором речь.
    const post = (icon, label, name, sub, actions) =>
      '<div class="duty">' +
      '<span class="duty__icon">' + icon + '</span>' +
      '<span class="duty__body">' +
      '<span class="duty__label">' + esc(label) + '</span>' +
      '<span class="duty__name' + (name ? '' : ' duty__name--empty') + '">' + esc(name || 'не назначен') + '</span>' +
      (sub ? '<span class="row__sub">' + esc(sub) + '</span>' : '') +
      '</span>' +
      '<span class="duty__acts">' + actions + '</span></div>';

    const act = (attr, id, label, kind) =>
      '<button type="button" class="duty__act' + (kind === 'drop' ? ' duty__act--drop' : ' duty__act--go') +
      '" ' + attr + '="' + id + '">' + esc(label) + '</button>';

    const activeRegions = data.items.filter((r) => r.is_active !== false);
    const archivedRegions = data.items.filter((r) => r.is_active === false);
    const rows = activeRegions.map((r) =>
      '<div class="card region">' +
      '<div class="region__head"><span class="region__name">' + esc(r.name) + '</span>' +
      (data.can_create_region
        ? '<button type="button" class="duty__act" data-edit-region="' + r.id + '">Название</button>'
        : '') + '</div>' +

      post(iconLeader(), 'Руководитель', r.leader_name, null,
        (r.can_assign_leader
          ? (r.leader_name
              ? act('data-drop-leader', r.id, 'Снять', 'drop')
              : act('data-assign-leader', r.id, 'Назначить', 'go'))
          : '')) +

      post(iconCurator(), 'Куратор', r.coordinator_name,
        // Остальные его отделения: снятие затрагивает их все.
        (r.coordinator_other_regions || []).length
          ? 'ведёт ещё: ' + r.coordinator_other_regions.join(', ')
          : null,
        (data.can_assign_coordinator
          ? (r.coordinator_user_id
              ? act('data-drop-coord', r.coordinator_user_id, 'Снять', 'drop')
              : act('data-assign-coord', r.id, 'Назначить', 'go'))
          : '')) +
      '</div>').join('');

    const archivedRows = archivedRegions.length
      ? '<div class="section-title">Архив</div>' + archivedRegions.map((r) =>
          '<div class="card region"><div class="region__head">' +
          '<span class="region__name">' + esc(r.name) + '</span>' +
          '<button type="button" class="duty__act duty__act--go" data-restore-region="' + r.id + '">Восстановить</button>' +
          '</div><div class="row__sub">Регистрация закрыта, данные сохранены</div></div>'
        ).join('')
      : '';

    setView(
      (rows || '<div class="empty">Активных отделений нет</div>') + archivedRows +
      (data.can_create_region
        ? '<button class="btn btn--block btn--dashed" id="regionsCreate">➕ Создать отделение</button>'
        : '')
    , gen);

    if (data.can_create_region) document.getElementById('regionsCreate').onclick = () => regionsCreateForm();

    on('[data-restore-region]', 'click', (event) => {
      const region = data.items.find((r) => r.id === Number(event.currentTarget.dataset.restoreRegion));
      if (!region) return;
      confirmAction({
        title: 'Восстановить регион?',
        body: 'Отделение «' + region.name + '» снова появится в рабочих списках. Для него будет создана новая ссылка регистрации.',
        confirmLabel: 'Восстановить',
      }, async () => {
        try {
          await api('/regions/' + region.id + '/restore', { method: 'POST' });
          toast('Регион восстановлен');
          startRender(renderRegionsTab);
        } catch (error) { fail(error); }
      });
    });

    on('[data-drop-leader]', 'click', (event) => {
      const region = data.items.find((r) => String(r.id) === event.currentTarget.dataset.dropLeader);
      if (!region) return;
      confirmAction({
        title: 'Снять руководителя?',
        body: region.leader_name + ' — отделение «' + region.name + '» останется без руководителя.',
        confirmLabel: 'Снять',
      }, async () => {
        try {
          await api('/regions/' + region.id + '/leader', { method: 'DELETE' });
          toast('Руководитель снят');
          startRender(renderRegionsTab);
        } catch (error) { fail(error); }
      });
    });

    on('[data-drop-coord]', 'click', (event) => {
      const id = Number(event.currentTarget.dataset.dropCoord);
      const region = data.items.find((r) => r.coordinator_user_id === id);
      if (!region) return;
      const all = [region.name].concat(region.coordinator_other_regions || []);
      confirmAction({
        title: 'Снять куратора?',
        body: region.coordinator_name + ' перестанет вести ' +
          (all.length > 1 ? all.length + ' отделения: ' + all.join(', ') : '«' + region.name + '»') + '.',
        confirmLabel: 'Снять',
      }, async () => {
        try {
          await api('/regions/coordinators/' + id, { method: 'DELETE' });
          toast('Куратор снят');
          startRender(renderRegionsTab);
        } catch (error) { fail(error); }
      });
    });

    on('[data-assign-leader]', 'click', (event) => {
      const regionId = Number(event.currentTarget.dataset.assignLeader);
      personPicker(regionId, async (memberId) => {
        try {
          await api('/regions/' + regionId + '/leader', { method: 'POST', body: { member_id: memberId } });
          toast('Руководитель назначен');
          closeModal();
          startRender(renderRegionsTab);
        } catch (error) { fail(error); }
      });
    });

    on('[data-assign-coord]', 'click', (event) => {
      const regionId = Number(event.currentTarget.dataset.assignCoord);
      coordinatorForm(data.items, regionId);
    });

    on('[data-edit-region]', 'click', (event) => {
      const region = data.items.find((r) => r.id === Number(event.currentTarget.dataset.editRegion));
      if (region) regionEditForm(region);
    });
  }

  function iconLeader() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' +
      '<circle cx="12" cy="8" r="3.2"></circle><path d="M4.5 20a7.5 7.5 0 0 1 15 0"></path></svg>';
  }

  function iconCurator() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M12 3l7 3v6c0 4.4-3 7.8-7 9-4-1.2-7-4.6-7-9V6z"></path></svg>';
  }

  // --- Заявки -----------------------------------------------------------------

  // Подтверждение анкет саморегистрации — второй вход в тот же
  // MembershipApplication, что и «📝 Подтверждения» в боте (см.
  // api/routers/applications.py); пока виден только superuser. Правка
  // полей анкеты («Исправить» в боте) сюда не переносим — редкий сценарий,
  // для него хватает бота.
  async function renderApplicationsTab(gen) {
    const data = await api('/applications/pending');

    const cards = data.items.length ? data.items.map((a) => {
      const eduLine = a.graduated_university
        ? 'Окончил ВУЗ'
        : 'Курс ' + (a.course != null ? a.course : '—') + (a.education_level_label ? ' · ' + esc(a.education_level_label) : '');
      return '<div class="card">' +
        '<div class="card__title">' + esc(a.full_name) + '</div>' +
        '<div class="row__sub">' + esc(a.region_name || '—') + ' · ' + esc(a.status_label) + '</div>' +
        '<div class="row__sub">' + esc(a.phone || '—') + ' · ' + dateRu(a.birth_date) + '</div>' +
        '<div class="row__sub">' + esc(a.university_name || 'ВУЗ не указан') + (a.faculty ? ' · ' + esc(a.faculty) : '') + '</div>' +
        '<div class="row__sub">' + esc(eduLine) + '</div>' +
        (a.workplace ? '<div class="row__sub">' + esc(a.workplace) + '</div>' : '') +
        '<div class="btn-row">' +
        '<button class="btn" data-approve="' + a.id + '">✅ Подтвердить</button>' +
        '<button class="btn btn--ghost" data-edit-application="' + a.id + '">✏️ Исправить</button>' +
        '<button class="btn btn--danger" data-reject="' + a.id + '">❌ Отклонить</button>' +
        '</div></div>';
    }).join('') : '<div class="empty">Анкет на подтверждении нет</div>';

    setView(cards, gen);

    on('[data-approve]', 'click', async (event) => {
      const id = event.currentTarget.dataset.approve;
      try {
        await api('/applications/' + id + '/approve', { method: 'POST' });
        toast('Подтверждено');
        await refreshMe();
        startRender(renderApplicationsTab);
      } catch (error) { fail(error); }
    });
    on('[data-edit-application]', 'click', (event) => {
      const id = Number(event.currentTarget.dataset.editApplication);
      const application = data.items.find((a) => a.id === id);
      if (application) applicationEditForm(application);
    });
    on('[data-reject]', 'click', (event) => {
      const id = event.currentTarget.dataset.reject;
      confirmAction('Отклонить анкету? Человек получит уведомление в боте.', async () => {
        try {
          await api('/applications/' + id + '/reject', { method: 'POST' });
          toast('Отклонено');
          await refreshMe();
          startRender(renderApplicationsTab);
        } catch (error) { fail(error); }
      });
    });
  }

  // Те же поля, что у «Исправить» в боте (handlers/apply.py::_EDIT_FIELDS) —
  // ФИО, телефон, дата рождения, факультет, место работы.
  function applicationEditForm(application) {
    modal('Исправить анкету',
      '<div class="field"><label>ФИО</label><input id="aeName" value="' + esc(application.full_name) + '" /></div>' +
      '<div class="field"><label>Телефон</label><input id="aePhone" value="' + esc(application.phone || '') + '" /></div>' +
      '<div class="field"><label>Дата рождения</label><input id="aeBirth" placeholder="20.02.2000" inputmode="numeric" value="' + esc(isoToRuDate(application.birth_date)) + '" /></div>' +
      '<div class="field"><label>Факультет</label><input id="aeFaculty" value="' + esc(application.faculty || '') + '" /></div>' +
      '<div class="field"><label>Место работы</label><input id="aeWorkplace" value="' + esc(application.workplace || '') + '" /></div>' +
      '<div class="btn-row"><button class="btn" id="aeSave">Сохранить</button>' +
      '<button class="btn btn--ghost" id="aeClose">Отмена</button></div>',
      () => {
        applyPhoneMask(document.getElementById('aePhone'));
        applyDateMask(document.getElementById('aeBirth'));
        document.getElementById('aeClose').onclick = closeModal;
        document.getElementById('aeSave').onclick = async () => {
          const birthRaw = document.getElementById('aeBirth').value;
          if (birthRaw && !ruDateToIso(birthRaw)) { toast('Проверьте дату рождения — формат ДД.ММ.ГГГГ'); return; }
          try {
            await api('/applications/' + application.id, {
              method: 'PATCH',
              body: {
                full_name: document.getElementById('aeName').value.trim(),
                phone: document.getElementById('aePhone').value.trim() || null,
                birth_date: ruDateToIso(birthRaw),
                faculty: document.getElementById('aeFaculty').value.trim() || null,
                workplace: document.getElementById('aeWorkplace').value.trim() || null,
              },
            });
            closeModal();
            toast('Сохранено');
            startRender(renderApplicationsTab);
          } catch (error) { fail(error); }
        };
      });
  }

  // --- Личный кабинет ---------------------------------------------------------

  // Профиль — экран просмотра: правка открывается кнопкой, а не висит
  // формой всё время. Герб рядом с именем носит только член Братства —
  // это знак посвящения; руководство обозначается отдельной строкой.
  function memberBadgeHtml(isMember) {
    if (!isMember) return '';
    return '<img class="badge-member" src="/static/badge-member.webp" alt="Член Братства" title="Член Братства">';
  }

  function roleLineHtml(roleTitle) {
    if (!roleTitle) return '';
    return '<div class="role-line">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v6c0 4.4-3 7.8-7 9-4-1.2-7-4.6-7-9V6z"></path></svg>' +
      '<span>' + esc(roleTitle) + '</span></div>';
  }

  // Бюро — отдельной строкой, а не вместо должности: человек бывает и
  // руководителем отделения, и куратором в бюро одновременно. Значок другой,
  // чтобы два золотых ряда не сливались в один.
  function bureauLineHtml(title) {
    if (!title) return '';
    return '<div class="role-line">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l2.4 5 5.6.8-4 3.9.9 5.5-4.9-2.6L7.1 18.2l.9-5.5-4-3.9L9.6 8z"></path></svg>' +
      '<span>Федеральное бюро · ' + esc(title) + '</span></div>';
  }

  function shortName(fullName) {
    const parts = (fullName || '').trim().split(/\s+/);
    return parts.slice(0, 2).join(' ');
  }

  function profileHeadHtml(data, name) {
    return '<div class="profile-top">' +
      (data.avatar
        ? '<img class="avatar avatar--lg" src="' + data.avatar + '" alt="">'
        : '<span class="avatar avatar--lg avatar--letter">' + esc((name || '?').trim()[0] || '?') + '</span>') +
      '<div class="profile-top__main">' +
      '<div class="profile-top__name"><span class="pf-name">' + esc(name) + '</span>' +
      memberBadgeHtml(data.is_member) + '</div>' +
      roleLineHtml(data.role_title) +
      bureauLineHtml(data.bureau_title) +
      '<div class="row__sub">' + esc(data.region_name || data.region || '') +
      (data.cell_name || data.cell ? ' · ' + esc(data.cell_name || data.cell) : '') + '</div>' +
      '</div></div>';
  }

  async function renderProfile(gen) {
    if (state.profileEdit) return renderProfileForm(gen);
    const profile = await api('/profile/me');

    setView(
      // Фамилия и имя, как в ленте и в чужом профиле: отчество осталось бы
      // единственным местом, где человек выглядит иначе, чем везде.
      profileHeadHtml(profile, shortName(profile.full_name)) +
      '<div class="btn-row"><button class="btn btn--block" id="pEdit">Редактировать</button></div>' +
      '<div id="profileTabBody" class="ptab-body"></div>'
    , gen);

    // Вкладок больше нет: публикации ушли вместе с личными постами, и
    // выбирать стало не из чего — в профиле осталось «О себе».
    const body = document.getElementById('profileTabBody');
    body.innerHTML = myAboutHtml(profile);
    wireProfileTabBody(profile);

    document.getElementById('pEdit').onclick = () => {
      state.profileEdit = true;
      startRender(renderProfile);
    };
  }

  // «Мои данные» — короткая сверка: человек смотрит, что о нём записано, и
  // правит, если устарело. Не витрина: смотреть её, кроме него, некому.
  //
  // Отсюда убраны две вещи. «Рассказ о себе» писался для тех, кто откроет
  // чужой профиль, — открывать его больше неоткуда, и текст уходил в пустоту:
  // руководство его не видит даже в «Составе», там для заметок своё поле.
  // Переключатель телефона прятал номер от тех же несуществующих читателей, а
  // руководство видит номер в «Составе» при любом его положении — то есть он
  // ничего не переключал.
  function myAboutHtml(profile) {
    return (profile.education && profile.education.length
        ? section('Учёба', '<div class="card card--rows">' + rowsHtml(profile.education) + '</div>')
        : '') +

      section('Контакты',
        '<div class="card card--rows">' +
        '<div class="row"><div class="row__main">' +
        '<div class="row__title">Телеграм</div>' +
        '<div class="row__sub">По нему с вами свяжется руководство</div></div>' +
        '<div class="row__side">' +
        (profile.telegram_username ? esc(profile.telegram_username) : '<span class="row__sub">не указан</span>') +
        '</div></div>' +
        '<div class="row"><div class="row__main">' +
        '<div class="row__title">Телефон</div></div>' +
        '<div class="row__side">' +
        (profile.phone ? esc(profile.phone) : '<span class="row__sub">не указан</span>') +
        '</div></div>' +
        '</div>' +
        '<div class="card__note">Эти данные видит руководство — в разделе «Состав». ' +
        'Если что-то устарело, поправьте кнопкой выше.</div>' +
        (profile.telegram_username ? '' :
          '<div class="notice">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 8v5M12 16v.01"></path></svg>' +
          '<div><div>Ник в телеграме не указан — с вами не смогут связаться.</div>' +
          '<button type="button" class="btn btn--small" id="pAskTelegram">Указать ник</button></div></div>'));
  }

  function section(title, html) {
    return '<div class="psection"><div class="section-title">' + esc(title) + '</div>' + html + '</div>';
  }

  function rowsHtml(rows) {
    return rows.map((row) =>
      '<div class="row"><div class="row__main"><div class="row__title">' + esc(row.label) + '</div></div>' +
      '<div class="row__side">' + esc(row.value) + '</div></div>').join('');
  }

  function wireProfileTabBody(profile) {
    const ask = document.getElementById('pAskTelegram');
    if (ask) ask.onclick = () => {
      state.profileEdit = true;
      startRender(renderProfile);
    };
  }

  async function renderProfileForm(gen) {
    const profile = await api('/profile/me');
    // «Под именем» в личном кабинете — статус (корпорант/член Братства/
    // выпускник), а не орг-роль из шапки: она про управление, не про это
    // место. Восстанавливается обратно в switchCabinet при переключении.
    setView(
      '<div class="card">' +
      '<div class="avatar-edit">' +
      (profile.avatar
        ? '<img class="avatar" src="' + profile.avatar + '" alt="">'
        : '<span class="avatar avatar--letter">' + esc((profile.full_name || '?').trim()[0] || '?') + '</span>') +
      '<div class="btn-row">' +
      '<button class="btn btn--small" id="pAvatarPick">Сменить фото</button>' +
      (profile.avatar ? '<button class="btn btn--small btn--ghost" id="pAvatarDrop">Убрать</button>' : '') +
      '</div>' +
      '<input type="file" id="pAvatarFile" accept="image/*" hidden />' +
      '</div>' +
      '<div class="card__title" style="margin-top:var(--space-12)">' + esc(profile.full_name) + '</div>' +
      '<div class="row__sub">' + esc(profile.status_label) + '</div>' +
      '<div class="row__sub">' + esc(profile.region_name || '') + (profile.cell_name ? ' · ' + esc(profile.cell_name) : '') + '</div>' +
      '</div>' +
      '<div class="card">' +
      '<div class="field"><label>Телефон</label><input id="pPhone" value="' + esc(profile.phone || '') + '" /></div>' +
      '<div class="field"><label>Telegram</label><input id="pTelegram" placeholder="@qwerty" value="' + esc(profile.telegram_username || '') + '" /></div>' +
      // Ручной ввод, а не календарь: нативное поле с датой рисуется системным
      // шрифтом, со своей кнопкой и своей минимальной шириной — на телефоне оно
      // вылезало за край карточки. Во всём остальном приложении дату вводят
      // так же (см. applyDateMask).
      '<div class="field"><label>Дата рождения</label><input id="pBirth" placeholder="20.02.2000" inputmode="numeric" value="' + esc(isoToRuDate(profile.birth_date)) + '" /></div>' +
      '<div class="field"><label>ВУЗ</label><input id="pUniversityInput" placeholder="Начните вводить название" autocomplete="off" value="' +
        esc(profile.university_name || '') + '" />' +
        '<div id="pUniversitySuggestions" class="chips" style="margin-top:var(--space-8)"></div></div>' +
      '<div class="field"><label>Факультет</label><input id="pFaculty" value="' + esc(profile.faculty || '') + '" /></div>' +
      '<div class="field"><label>Курс</label><select id="pCourse">' +
      '<option value="">—</option>' +
      [1, 2, 3, 4, 5, 6].map((n) => '<option value="' + n + '"' + (profile.course === n ? ' selected' : '') + '>' + n + '</option>').join('') +
      '<option value="graduated"' + (profile.graduated_university ? ' selected' : '') + '>Окончил</option>' +
      '</select></div>' +
      '<div class="field" id="pEducationLevelField"' + (profile.graduated_university ? ' hidden' : '') + '><label>Уровень обучения</label><select id="pEducationLevel">' +
      '<option value="">—</option>' +
      ['bachelor:Бакалавриат', 'master:Магистратура', 'postgraduate:Аспирантура', 'residency:Ординатура'].map((pair) => {
        const [value, label] = pair.split(':');
        return '<option value="' + value + '"' + (profile.education_level === value ? ' selected' : '') + '>' + label + '</option>';
      }).join('') +
      '</select></div>' +
      '<div class="field"><label>Место работы</label><input id="pWorkplace" value="' + esc(profile.workplace || '') + '" /></div>' +
      '<div class="btn-row"><button class="btn" id="pSave">Сохранить</button>' +
      '<button class="btn btn--ghost" id="pCancel">Отмена</button></div>' +
      '</div>'
    , gen);
    applyPhoneMask(document.getElementById('pPhone'));
    document.getElementById('pCancel').onclick = () => {
      state.profileEdit = false;
      startRender(renderProfile);
    };

    // Свой круглый аватар — виден в ленте и комментариях. Файл уходит
    // отдельным запросом, а не вместе с формой: он и сохраняется отдельно.
    const avatarFile = document.getElementById('pAvatarFile');
    document.getElementById('pAvatarPick').onclick = () => avatarFile.click();
    avatarFile.onchange = async () => {
      if (!avatarFile.files.length) return;
      const form = new FormData();
      form.append('file', avatarFile.files[0]);
      try {
        await api('/profile/me/avatar', { method: 'POST', body: form });
        toast('Фото обновлено');
        startRender(renderProfile);
      } catch (error) { fail(error); }
    };
    const avatarDrop = document.getElementById('pAvatarDrop');
    if (avatarDrop) {
      avatarDrop.onclick = () => confirmAction('Убрать фото?', async () => {
        try {
          await api('/profile/me/avatar', { method: 'DELETE' });
          toast('Фото убрано');
          startRender(renderProfile);
        } catch (error) { fail(error); }
      });
    }

    const pCourseSelect = document.getElementById('pCourse');
    const pEducationLevelField = document.getElementById('pEducationLevelField');
    pCourseSelect.addEventListener('change', () => {
      const graduated = pCourseSelect.value === 'graduated';
      pEducationLevelField.hidden = graduated;
      if (graduated) document.getElementById('pEducationLevel').value = '';
    });

    // Поиск вуза — тот же паттерн, что в форме «Состав» (memberForm): два
    // независимых места используют одинаковый виджет, не выносил в общую
    // функцию ради двух вызовов — см. правило проекта против преждевременных
    // абстракций.
    let selectedUniversityId = profile.university_id;
    const uniInput = document.getElementById('pUniversityInput');
    const uniSuggestions = document.getElementById('pUniversitySuggestions');
    let uniTimer;
    uniInput.addEventListener('input', () => {
      selectedUniversityId = null;
      clearTimeout(uniTimer);
      const q = uniInput.value.trim();
      if (!q) { uniSuggestions.innerHTML = ''; return; }
      uniTimer = setTimeout(async () => {
        try {
          const data = await api('/universities?region_id=' + profile.region_id + '&q=' + encodeURIComponent(q));
          uniSuggestions.innerHTML = data.items.map((u) =>
            '<button type="button" class="chip" data-uni="' + u.id + '" data-name="' + esc(u.name) + '">' + esc(u.name) + '</button>'
          ).join('');
          on('[data-uni]', 'click', (event) => {
            selectedUniversityId = Number(event.currentTarget.dataset.uni);
            uniInput.value = event.currentTarget.dataset.name;
            uniSuggestions.innerHTML = '';
          }, uniSuggestions);
        } catch (error) { /* автодополнение необязательно — молча пропускаем сбой */ }
      }, 300);
    });

    applyDateMask(document.getElementById('pBirth'));

    document.getElementById('pSave').onclick = async () => {
      const birthRaw = document.getElementById('pBirth').value.trim();
      if (birthRaw && !ruDateToIso(birthRaw)) { toast('Проверьте дату рождения — формат ДД.ММ.ГГГГ'); return; }

      const courseRaw = document.getElementById('pCourse').value;
      const graduatedUniversity = courseRaw === 'graduated';
      const workplace = document.getElementById('pWorkplace').value.trim() || null;
      if (graduatedUniversity && !workplace) { toast('Укажите место работы'); return; }

      const uniName = uniInput.value.trim();
      const uniProblem = universityNameProblem(uniName);
      if (uniProblem) { toast(uniProblem); return; }
      let universityId = selectedUniversityId;
      if (!uniName) {
        universityId = null;
      } else if (!universityId) {
        try {
          const created = await api('/universities', { method: 'POST', body: { name: uniName } });
          universityId = created.id;
        } catch (error) { fail(error); return; }
      }
      const payload = {
        phone: document.getElementById('pPhone').value.trim() || null,
        telegram_username: document.getElementById('pTelegram').value.trim() || null,
        birth_date: ruDateToIso(birthRaw),
        university_id: universityId,
        faculty: document.getElementById('pFaculty').value.trim() || null,
        course: graduatedUniversity ? null : (courseRaw ? Number(courseRaw) : null),
        graduated_university: graduatedUniversity,
        education_level: graduatedUniversity ? null : (document.getElementById('pEducationLevel').value || null),
        workplace: workplace,
      };
      try {
        await api('/profile/me', { method: 'PATCH', body: payload });
        toast('Сохранено');
        state.profileEdit = false;
        startRender(renderProfile);
      } catch (error) { fail(error); }
    };
  }

  // --- Академия (геймификация, MVP) ---------------------------------------------
  // Персонаж — не собирается из частей, а выбирается из готовых образов
  // (картинки отдаёт бэкенд, см. api/routers/character.py::OUTFITS).

  const TIER_LABELS = { bronze: 'Бронза', silver: 'Серебро', gold: 'Золото' };
  // Образы с видео вместо статичной картинки — файлы {id}_wave.mp4 (жест,
  // играет один раз при входе) и {id}_idle.mp4 (дальше зацикленный кадр)
  // в webapp/avatars/. У остальных образов таких файлов нет.
  const VIDEO_OUTFITS = ['polo', 'ataman'];

  // Радар «к чему тяготеет» — N осей (ролей) по кругу, значения 0-100.
  function buildRadarSvg(points) {
    const n = points.length;
    if (!n) return '';
    const size = 220, cx = size / 2, cy = size / 2, r = size / 2 - 52;
    const angleFor = (i) => -Math.PI / 2 + (i * 2 * Math.PI) / n;
    const ringPoints = (frac) => points.map((_, i) => {
      const a = angleFor(i);
      return (cx + Math.cos(a) * r * frac).toFixed(1) + ',' + (cy + Math.sin(a) * r * frac).toFixed(1);
    }).join(' ');
    const grid = [0.33, 0.66, 1].map((f) =>
      '<polygon points="' + ringPoints(f) + '" fill="none" stroke="var(--border)" stroke-width="1"/>').join('');
    const axes = points.map((_, i) => {
      const a = angleFor(i);
      const x = (cx + Math.cos(a) * r).toFixed(1), y = (cy + Math.sin(a) * r).toFixed(1);
      return '<line x1="' + cx + '" y1="' + cy + '" x2="' + x + '" y2="' + y + '" stroke="var(--border)" stroke-width="1"/>';
    }).join('');
    const dataPoly = points.map((p, i) => {
      const a = angleFor(i);
      const frac = Math.max(0, Math.min(100, p.value)) / 100;
      return (cx + Math.cos(a) * r * frac).toFixed(1) + ',' + (cy + Math.sin(a) * r * frac).toFixed(1);
    }).join(' ');
    const labels = points.map((p, i) => {
      const a = angleFor(i);
      const cosA = Math.cos(a);
      const lx = (cx + cosA * (r + 20)).toFixed(1), ly = (cy + Math.sin(a) * (r + 20)).toFixed(1);
      const anchor = cosA > 0.3 ? 'start' : cosA < -0.3 ? 'end' : 'middle';
      return '<text x="' + lx + '" y="' + ly + '" text-anchor="' + anchor + '" dominant-baseline="middle" font-size="11" fill="var(--text)">' +
        esc(p.label) + '</text>';
    }).join('');
    return '<svg viewBox="0 0 ' + size + ' ' + size + '" style="width:100%;max-width:260px;display:block;margin:0 auto;overflow:visible">' +
      grid + axes +
      '<polygon points="' + dataPoly + '" fill="var(--button)" fill-opacity="0.35" stroke="var(--button)" stroke-width="2"/>' +
      labels + '</svg>';
  }

  // Группировка плоского списка заданий по роли — порядок ролей как при
  // первом упоминании внутри списка (бэкенд уже отдаёт их стабильно).
  function groupQuestsByBranch(quests) {
    const groups = [];
    const byLabel = {};
    quests.forEach((q) => {
      const label = q.branch_label || 'Другое';
      if (!byLabel[label]) {
        byLabel[label] = { label, quests: [] };
        groups.push(byLabel[label]);
      }
      byLabel[label].quests.push(q);
    });
    return groups;
  }

  function outfitPickerModal(data) {
    const cards = data.outfits.map((o) => {
      const locked = !o.unlocked;
      return (
        '<div class="outfit-card' + (locked ? ' outfit-card--locked' : '') +
        (o.id === data.active_outfit ? ' outfit-card--active' : '') + '"' +
        (locked ? '' : ' data-outfit="' + o.id + '"') + '>' +
        '<img src="' + o.image + '" alt="' + esc(o.label) + '">' +
        '<div class="outfit-card__label">' + esc(o.label) + '</div>' +
        (locked ? '<div class="outfit-card__hint">🔒 ' + esc(o.hint || '') + '</div>' : '') +
        '</div>'
      );
    }).join('');

    modal('Выбор образа',
      '<div class="outfit-grid">' + cards + '</div>' +
      '<div class="btn-row"><button class="btn btn--ghost" id="outfitClose">Закрыть</button></div>',
      () => {
        document.getElementById('outfitClose').onclick = closeModal;
        on('[data-outfit]', 'click', async (event) => {
          const outfit = event.currentTarget.dataset.outfit;
          try {
            await api('/character/me/avatar', { method: 'PATCH', body: { outfit } });
            closeModal();
            toast('Образ выбран');
            startRender(renderCharacter);
          } catch (error) { fail(error); }
        });
      });
  }

  function renderAvatarMeta(data, active) {
    const questsCompleted = data.quests.filter((q) => q.completed).length;
    return esc(active.label) +
      (data.tier ? ' · Уровень: ' + TIER_LABELS[data.tier] : '') +
      '<br>Заданий выполнено: ' + questsCompleted + '/' + data.quests.length + ' · ⭐ ' + data.stars;
  }

  function renderQuestsCard(data) {
    const branchTabs = data.branches.map((b) =>
      '<button type="button" class="chip' + (b.id === state.lobbyBranch ? ' chip--active' : '') +
      '" data-lobby-branch="' + b.id + '">' + esc(b.label) +
      (b.claimable_stars > 0 ? ' <span class="dot dot--alert"></span>' : '') + '</button>'
    ).join('');

    const branchQuests = data.quests.filter((q) => q.branch_id === state.lobbyBranch);
    const questsHtml = branchQuests.map((q) => {
      const pct = q.next_target ? Math.max(0, Math.min(100, Math.round((q.count / q.next_target) * 100))) : 100;
      return '<div class="row"><div class="row__main"><div class="row__title">' + esc(q.title) + '</div>' +
        '<div class="quest-bar"><div class="quest-bar__fill" style="width:' + pct + '%"></div></div>' +
        '<div class="row__sub">' + esc(q.progress_label) + '</div></div>' +
        '<div class="row__side">' +
        (q.reward_outfit
          ? '<div class="reward-skin"><img src="' + q.reward_outfit.image + '" alt="' + esc(q.reward_outfit.label) + '"><span>' + esc(q.reward_outfit.label) + '</span></div>'
          : q.claimable_stars > 0
            ? '<button class="btn btn--small btn--claim" data-claim-quest="' + q.id + '">Получить <span class="reward-badge">' + q.claimable_stars + ' ⭐</span></button>'
            : q.next_target !== null
              ? '<span class="reward-badge reward-badge--pending">' + q.next_target + ' ⭐</span>'
              : '') +
        '</div></div>';
    }).join('');

    return '<div class="chips" style="margin-bottom:var(--space-12)">' + branchTabs + '</div>' +
      (questsHtml || '<div class="empty">Заданий пока нет</div>');
  }

  function wireQuestsCard(data) {
    on('[data-lobby-branch]', 'click', async (event) => {
      const branchId = event.currentTarget.dataset.lobbyBranch;
      state.lobbyBranch = branchId;
      document.getElementById('questsCard').innerHTML = renderQuestsCard(data);
      wireQuestsCard(data);
    });
    on('[data-claim-quest]', 'click', async (event) => {
      const questId = Number(event.currentTarget.dataset.claimQuest);
      try {
        await api('/character/me/quests/' + questId + '/claim', { method: 'POST' });
        toast('Звёзды получены');
        const fresh = await api('/character/me');
        const active = fresh.outfits.find((o) => o.id === fresh.active_outfit) || fresh.outfits[0];
        document.getElementById('avatarMeta').innerHTML = renderAvatarMeta(fresh, active);
        document.getElementById('questsCard').innerHTML = renderQuestsCard(fresh);
        wireQuestsCard(fresh);
      } catch (error) { fail(error); }
    });
  }

  async function renderCharacter(gen) {
    let data = await api('/character/me');

    if (!state.lobbyBranch || !data.branches.some((b) => b.id === state.lobbyBranch)) {
      const withClaimable = data.branches.find((b) => b.claimable_stars > 0);
      state.lobbyBranch = (withClaimable || data.branches[0] || {}).id || null;
    }
    const active = data.outfits.find((o) => o.id === data.active_outfit) || data.outfits[0];

    // Жест приветствия пока сделан только для части образов (см.
    // VIDEO_OUTFITS) — играет при каждом входе во вкладку «Академия», затем
    // плавно сменяется idle-циклом. У остальных образов такого кадра нет,
    // просто картинка. Видео живёт в #avatarHolder, который переключение
    // вкладок ролей ниже не трогает — иначе оно бы перезапускалось при
    // каждом клике.
    const showWave = VIDEO_OUTFITS.indexOf(active.id) !== -1;

    setView(
      '<div class="card" style="text-align:center">' +
      '<div id="avatarHolder" style="width:220px;max-width:100%;aspect-ratio:3/4;margin:0 auto;border-radius:16px;overflow:hidden;position:relative">' +
      (showWave
        ? '<video src="/static/avatars/' + active.id + '_wave.mp4" id="avatarWaveVideo" autoplay muted playsinline style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:1;transition:opacity .6s ease"></video>' +
          '<video src="/static/avatars/' + active.id + '_idle.mp4" id="avatarIdleVideo" muted playsinline loop style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:0;transition:opacity .6s ease"></video>'
        : '<img src="' + active.image + '" alt="' + esc(active.label) + '" class="avatar-idle" style="width:100%;height:100%;object-fit:cover;display:block">') +
      '</div>' +
      '<div class="row__sub" id="avatarMeta" style="margin-top:var(--space-8)">' + renderAvatarMeta(data, active) + '</div>' +
      '<button class="btn" id="avatarEdit" style="margin-top:var(--space-12)">Сменить образ</button>' +
      '</div>' +
      '<div class="card">' + buildRadarSvg(data.radar) + '</div>' +
      '<div class="card"><div class="card__title">Задания</div><div id="questsCard">' + renderQuestsCard(data) + '</div></div>'
    , gen);

    if (showWave) {
      const waveVideo = document.getElementById('avatarWaveVideo');
      const idleVideo = document.getElementById('avatarIdleVideo');
      waveVideo.addEventListener('ended', () => {
        waveVideo.style.opacity = '0';
        idleVideo.style.opacity = '1';
        idleVideo.play().catch(() => {});
      });
    }

    document.getElementById('avatarEdit').onclick = () => outfitPickerModal(data);
    wireQuestsCard(data);
  }

  // --- Магазин (геймификация, MVP) --------------------------------------

  // Товары за рубли, ведущие на внешний магазин. Настоящие стоят первыми и
  // ведут на живую карточку товара; помеченные sample — образцы вёрстки,
  // оставшиеся с тех пор, когда настоящих не было ни одного: у них
  // выдуманные цены, отзывы и ссылка на главную страницу магазина.
  // Отзывы и рейтинг есть только у образцов — у настоящего товара мы их не
  // придумываем, вместо них подписан магазин, куда ведёт ссылка.
  const RUBLE_MERCH = [
    { id: 's-nami-bog', category: 'clothing', icon: '👕', title: 'Футболка «С нами Бог!»',
      price: 4990, oldPrice: null, shop: 'tsargrad.shop',
      fit: 'Обычная посадка', fabric: '100% хлопок', color: 'Чёрная',
      // Первой — спина: на ней вся печать, ради неё футболку и берут.
      // Дальше перёд и бирка. Снимки лежат в webapp/merch/.
      photos: ['/static/merch/s-nami-bog-1.jpg', '/static/merch/s-nami-bog-2.jpg', '/static/merch/s-nami-bog-3.jpg'],
      url: 'https://tsargrad.shop/catalogue/futbolki/futbolka-s-nami-bog' },
    { id: 'georgiy', category: 'clothing', icon: '👕', title: 'Футболка «св. Георгий Победоносец»',
      price: 4990, oldPrice: null, shop: 'tsargrad.shop',
      fit: 'Оверсайз', fabric: '100% хлопок', color: 'Серая',
      // Перёд с иконой первым, потом спина, потом два студийных снимка.
      photos: ['/static/merch/georgiy-1.jpg', '/static/merch/georgiy-2.jpg',
               '/static/merch/georgiy-3.jpg', '/static/merch/georgiy-4.jpg'],
      url: 'https://tsargrad.shop/catalogue/futbolki/futbolka-sv-georgii-pobedonosets' },
    { id: 'krest', category: 'clothing', icon: '👕', title: 'Футболка «Цареградский крест»',
      price: 4990, oldPrice: null, shop: 'tsargrad.shop',
      fit: 'Оверсайз', fabric: '95% хлопок, 5% эластан', color: 'Белая',
      photos: ['/static/merch/krest-1.jpg', '/static/merch/krest-2.jpg', '/static/merch/krest-3.jpg'],
      url: 'https://tsargrad.shop/catalogue/futbolki/futbolka-tsaregradskii-krest-lik-sv-georgiya-pobedonostsa' },
    // Образцы одежды убраны: рядом с настоящими футболками выдуманное худи со
    // ссылкой на главную Ozon выглядело обманом. В «Книгах» настоящих товаров
    // пока нет, там образцы остаются — и помечены как образцы.
    { category: 'books', sample: true, icon: '📖', title: 'Молитвослов Братства', price: 600, oldPrice: null, rating: '5.0', reviews: 6, url: 'https://www.ozon.ru' },
    { category: 'books', sample: true, icon: '📚', title: 'Книга «История Братства»', price: 950, oldPrice: 1200, rating: '4.8', reviews: 9, url: 'https://www.ozon.ru' },
  ];
  const RUBLE_FILTERS = [['clothing', 'Одежда'], ['books', 'Книги']];
  const TOKEN_FILTERS = [['outfit', 'Скины'], ['physical', 'Мерч']];

  function tokenCard(item, purchasesOpen) {
    // Пока покупка закрыта, вместо кнопки — цена и куда идти. Цену прячем
    // не зря: человек копит именно на неё, и убрать её значило бы отнять
    // смысл у звёзд, пока магазин настраивается.
    const bottom = item.owned
      ? '<div class="row__sub" style="margin-top:var(--space-8)">Куплено</div>'
      : !purchasesOpen
      ? '<div class="row__sub" style="margin-top:var(--space-8)">' + item.price + ' ⭐ · пока недоступно</div>'
      : '<button class="btn btn--small' + (item.affordable ? '' : ' btn--ghost') +
        '" data-buy-item="' + item.id + '" style="margin-top:var(--space-8);width:100%"' + (item.affordable ? '' : ' disabled') + '>Купить за ' + item.price + ' ⭐</button>';
    // У вещи со снимками картинка открывается на весь экран — рассмотреть,
    // за что отдаёшь звёзды. У образа этого не нужно: его и так примеряют.
    const photos = item.photos || [];
    return (
      '<div class="ozon-card">' +
      '<div class="ozon-card__img"' + (photos.length ? ' data-token-photos="' + esc(item.id) + '"' : '') + '>' +
      (photos.length
        ? '<img src="' + esc(photos[0]) + '" alt="' + esc(item.label) + '" class="ozon-card__photo">'
        : item.image
        ? '<img src="' + item.image + '" alt="' + esc(item.label) + '" style="width:100%;height:100%;object-fit:contain' + (item.owned ? '' : ';filter:grayscale(.3)') + '">'
        : '🎁') +
      '</div>' +
      '<div class="ozon-card__title">' + esc(item.label) + '</div>' +
      '<div class="ozon-card__rating">' + esc(item.description) + '</div>' +
      bottom +
      '</div>'
    );
  }

  async function shopTokensBody() {
    const data = await api('/shop');
    if (!state.shopFilter) state.shopFilter = TOKEN_FILTERS[0][0];
    const filterTabs = '<div class="chips" style="margin-bottom:var(--space-12)">' +
      TOKEN_FILTERS.map((f) => '<button type="button" class="chip' + (state.shopFilter === f[0] ? ' chip--active' : '') +
        '" data-shop-filter="' + f[0] + '">' + f[1] + '</button>').join('') + '</div>';
    const filtered = data.items.filter((item) => item.kind === state.shopFilter);
    return {
      html: '<div class="card" style="text-align:center"><div class="card__title">Баланс</div><div style="font-size:22px">⭐ ' + data.stars + '</div></div>' +
        (data.purchases_open ? '' :
          '<div class="notice">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 8v5M12 16v.01"></path></svg>' +
          '<div>' + esc(data.closed_hint || 'Покупка за звёзды пока недоступна') + '</div></div>') +
        filterTabs + '<div class="ozon-grid">' +
        filtered.map((item) => tokenCard(item, data.purchases_open)).join('') + '</div>',
      wire: () => {
        on('[data-shop-filter]', 'click', (event) => {
          state.shopFilter = event.currentTarget.dataset.shopFilter;
          startRender(renderShop);
        });
        on('[data-token-photos]', 'click', (event) => {
          const found = data.items.find((i) => i.id === event.currentTarget.dataset.tokenPhotos);
          if (found) photoViewer(found.photos.map((url) => ({ url: url })), 0);
        });
        on('[data-buy-item]', 'click', async (event) => {
          const itemId = event.currentTarget.dataset.buyItem;
          try {
            await api('/shop/' + itemId + '/buy', { method: 'POST' });
            toast('Покупка совершена');
            startRender(renderShop);
          } catch (error) { fail(error); }
        });
      },
    };
  }

  function rubleMerchCard(item) {
    const discountPct = item.oldPrice ? Math.round((1 - item.price / item.oldPrice) * 100) : null;
    // Подпись под ценой: у образца — выдуманный рейтинг, у настоящего товара
    // магазин и краткое описание. Отзывов у него нет, и рисовать их нечем.
    const foot = item.sample
      ? '★ ' + item.rating + ' · ' + item.reviews + ' отзывов'
      : esc(item.fit) + ' · ' + esc(item.fabric);
    const photos = item.photos || [];
    // У кого есть снимки — карточка открывает товар со всеми фотографиями,
    // у образца открывать нечего, он так и остаётся ссылкой наружу.
    const tag = photos.length ? 'div' : 'a';
    const attrs = photos.length
      ? ' data-merch="' + esc(item.id) + '"'
      : ' href="' + esc(item.url) + '" target="_blank" rel="noopener" data-ext="' + esc(item.url) + '"';
    return (
      '<' + tag + ' class="ozon-card"' + attrs + '>' +
      '<div class="ozon-card__img">' +
      (photos.length
        ? '<img src="' + esc(photos[0]) + '" alt="' + esc(item.title) + '" class="ozon-card__photo">'
        : item.icon) +
      (item.sample ? '<span class="ozon-card__discount ozon-card__discount--sample">образец</span>'
        : discountPct ? '<span class="ozon-card__discount">-' + discountPct + '%</span>' : '') + '</div>' +
      '<div class="ozon-card__price">' +
      '<span class="ozon-card__price-now">' + item.price + ' ₽</span>' +
      (item.oldPrice ? '<span class="ozon-card__price-old">' + item.oldPrice + ' ₽</span>' : '') +
      '</div>' +
      '<div class="ozon-card__title">' + esc(item.title) + '</div>' +
      '<div class="ozon-card__rating">' + foot + '</div>' +
      '</' + tag + '>'
    );
  }

  // Карточка товара: все снимки подряд, цена и одна кнопка — в магазин.
  // Купить внутри приложения нельзя, деньги идут мимо нас, и делать вид, что
  // покупка происходит здесь, было бы враньём.
  function merchModal(item) {
    modal(item.title,
      '<div class="merch-shots">' +
      (item.photos || []).map((src, i) =>
        '<img src="' + esc(src) + '" alt="' + esc(item.title) + ' — снимок ' + (i + 1) + '" class="merch-shots__img">').join('') +
      '</div>' +
      '<div class="card">' +
      '<div class="row"><div class="row__main">Цена</div><div class="row__side">' + item.price + ' ₽</div></div>' +
      '<div class="row"><div class="row__main">Посадка</div><div class="row__side">' + esc(item.fit) + '</div></div>' +
      '<div class="row"><div class="row__main">Состав</div><div class="row__side">' + esc(item.fabric) + '</div></div>' +
      '<div class="row"><div class="row__main">Цвет</div><div class="row__side">' + esc(item.color) + '</div></div>' +
      '<div class="row"><div class="row__main">Магазин</div><div class="row__side">' + esc(item.shop) + '</div></div>' +
      '</div>' +
      '<div class="btn-row"><button class="btn" id="merchOpen">Открыть в магазине</button>' +
      '<button class="btn btn--ghost" id="merchClose">Закрыть</button></div>',
      () => {
        document.getElementById('merchClose').onclick = closeModal;
        document.getElementById('merchOpen').onclick = () => {
          if (tg && typeof tg.openLink === 'function') tg.openLink(item.url);
          else window.open(item.url, '_blank', 'noopener');
        };
      });
  }

  function shopRublesBody() {
    if (!state.shopFilter) state.shopFilter = RUBLE_FILTERS[0][0];
    const filterTabs = '<div class="chips" style="margin-bottom:var(--space-12)">' +
      RUBLE_FILTERS.map((f) => '<button type="button" class="chip' + (state.shopFilter === f[0] ? ' chip--active' : '') +
        '" data-shop-filter="' + f[0] + '">' + f[1] + '</button>').join('') + '</div>';
    const filtered = RUBLE_MERCH.filter((item) => item.category === state.shopFilter);
    const samples = filtered.filter((item) => item.sample).length;
    return {
      // Пока настоящих товаров мало, честнее сказать, сколько тут образцов,
      // чем объявлять ненастоящим весь раздел: одна карточка уже живая.
      html: (samples
        ? '<div class="empty" style="margin-bottom:var(--space-12)">Помеченные «образец» — пример вёрстки: цены и ссылки у них ненастоящие</div>'
        : '') +
        filterTabs + '<div class="ozon-grid">' + filtered.map(rubleMerchCard).join('') + '</div>',
      wire: () => {
        on('[data-shop-filter]', 'click', (event) => {
          state.shopFilter = event.currentTarget.dataset.shopFilter;
          startRender(renderShop);
        });
        // Внутри Телеграма обычная ссылка в новой вкладке открывается не
        // всегда — у мини-приложения для этого свой вызов.
        on('[data-ext]', 'click', (event) => {
          if (!tg || typeof tg.openLink !== 'function') return;
          event.preventDefault();
          tg.openLink(event.currentTarget.dataset.ext);
        });
        on('[data-merch]', 'click', (event) => {
          const found = RUBLE_MERCH.find((i) => i.id === event.currentTarget.dataset.merch);
          if (found) merchModal(found);
        });
      },
    };
  }

  async function renderShop(gen) {
    if (!state.shopTab) state.shopTab = 'tokens';
    const tabsHtml = '<div class="chips" style="margin-bottom:var(--space-12)">' +
      '<button type="button" class="chip' + (state.shopTab === 'tokens' ? ' chip--active' : '') + '" data-shop-tab="tokens">Токены</button>' +
      '<button type="button" class="chip' + (state.shopTab === 'rubles' ? ' chip--active' : '') + '" data-shop-tab="rubles">Рубли</button>' +
      '</div>';
    const body = state.shopTab === 'tokens' ? await shopTokensBody() : shopRublesBody();
    setView(tabsHtml + body.html, gen);
    on('[data-shop-tab]', 'click', (event) => {
      state.shopTab = event.currentTarget.dataset.shopTab;
      state.shopFilter = null;
      startRender(renderShop);
    });
    body.wire();
  }

  // --- Новости --------------------------------------------------------------
  // Писать может любой, видят все. Роль решает не аудиторию, а подпись под
  // постом, поэтому в форме показываем строку «опубликуется как»
  // (services/news.py::byline_for).

  // --- Лента новостей ---------------------------------------------------------
  // Работает как канал: посты снизу вверх по времени, экран открывается на
  // свежем, реакции и счётчик просмотров под постом. У участника этой же
  // лентой служит «Главная» — над ней закреплены его личные карточки.

  async function renderHome(gen) { return renderFeed(gen); }
  async function renderNews(gen) { return renderFeed(gen); }

  // Личный кабинет — не место для управления чужим. Сервер решает то же самое
  // сам (services/news.py::can_delete_post), это лишь говорит ему, откуда
  // смотрят: одна и та же лента открывается из обоих кабинетов.
  function feedScope() {
    return state.cabinetMode === 'personal' ? '?personal=true' : '';
  }

  async function renderFeed(gen) {
    renderedPosts = 0;
    const data = await api('/news' + feedScope());

    // Разделителей по дням больше нет: время у каждого поста теперь со днём
    // («сегодня в 18:20», «22 авг в 14:05»), и плашка между постами только
    // рвала ленту пополам.
    const posts = data.items.map((item) => newsPostHtml(item, data.reaction_set || [])).join('');

    setView(
      (data.can_post ? composeRowHtml(data.me_avatar, data.me_name) : '') +
      (data.items.length ? '<div class="feed">' + posts + '</div>' : '<div class="empty">Новостей пока нет</div>')
    , gen);

    if (data.can_post) document.getElementById('addNews').onclick = () => newsForm().catch(fail);
    wireFeed(data);
  }

  function newsPostHtml(item, reactionSet) {
    const reactions = item.reactions || [];
    const liked = reactions.find((r) => r.emoji === '❤️' && r.mine);
    const likeCount = reactions.reduce((sum, r) => sum + r.count, 0);

    // Шапка: круглый аватар и подпись сверху. Профиль по ней не открывается
    // ни у кого — в ленте теперь говорит ячейка, отделение или Братство, и за
    // подписью стоит организация, а не человек. Ходить из ленты в чужие
    // карточки стало незачем.
    const head = '<div class="post__head">' +
      '<div class="post__author post__author--static">' +
      avatarHtml(item.avatar, item.byline) +
      '<span class="post__byline">' + esc(item.byline || item.author_name || '') + '</span>' +
      '</div>' +
      '<span class="post__time">' + timeRu(item.created_at) + '</span>' +
      (item.can_delete
        ? '<button type="button" class="post__menu" data-del-news="' + item.id + '" aria-label="Действия с постом">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">' +
          '<circle cx="12" cy="5" r="1"></circle><circle cx="12" cy="12" r="1"></circle><circle cx="12" cy="19" r="1"></circle>' +
          '</svg></button>'
        : '') +
      '</div>';

    // Комментарии убраны: лента — объявления движения, а не обсуждение.
    // Отклик остался один, безымянный, — сердце. Оно говорит «прочитал и
    // согласен», не втягивая в разговор.
    const actions = '<div class="post__actions">' +
      '<button type="button" class="act' + (liked ? ' act--on' : '') + '" data-like="' + item.id + '">' +
      iconLike(Boolean(liked)) + '<span>' + (likeCount || '') + '</span></button>' +
      '<span class="act act--views">' + iconEye() + '<span>' + (item.views || 0) + '</span></span>' +
      '</div>';

    return '<div class="post" data-post="' + item.id + '">' +
      head +
      newsPhotosHtml(item) +
      (item.text ? postTextHtml(item) : '') +
      actions +
      '</div>';
  }

  // Длинный пост в ленте сворачиваем. Порог по числу знаков, а не по высоте:
  // высоту до отрисовки не знать, а перемерять каждый пост после — дёргать
  // вёрстку. Восемь строк примерно и есть эти четыреста знаков.
  const POST_CLAMP_CHARS = 400;

  function postTextHtml(item) {
    const long = (item.text || '').length > POST_CLAMP_CHARS;
    return '<div class="post__body">' +
      '<div class="post__text' + (long ? ' post__text--clamped' : '') + '">' + esc(item.text) + '</div>' +
      (long ? '<button type="button" class="post__more" data-more="' + item.id + '">Показать ещё</button>' : '') +
      '</div>';
  }

  // Круглый аватар; если его нет — кружок с первой буквой подписи.
  function avatarHtml(url, name) {
    if (url) return '<img class="avatar" src="' + url + '" alt="">';
    const letter = ((name || '?').trim()[0] || '?').toUpperCase();
    return '<span class="avatar avatar--letter">' + esc(letter) + '</span>';
  }

  function iconLike(filled) {
    return '<svg viewBox="0 0 24 24" fill="' + (filled ? 'currentColor' : 'none') +
      '" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M7 21V10l4.5-7a2 2 0 0 1 2.9 2.4L13 9h5.4a2 2 0 0 1 2 2.5l-1.7 7A2.5 2.5 0 0 1 16.2 21z"/>' +
      '<path d="M7 10H4v11h3"/></svg>';
  }

  function iconEye() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" ' +
      'stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s4-6 10-6 10 6 10 6-4 6-10 6-10-6-10-6z"/>' +
      '<circle cx="12" cy="12" r="2.5"/></svg>';
  }

  // Альбом как в Telegram: одна во всю ширину, две в ряд, три — крупная
  // слева и две справа, четыре и больше — сетка с «+N» на последней.
  // eager у первых постов: приложение при входе разворачивается на весь экран
  // и меняет высоту окна уже после отрисовки. Отложенная загрузка успевает
  // решить, что картинка «за экраном», и повторно этого не пересматривает —
  // фотография так и висит пустой, пока не прокрутишь. Вся лента весит меньше
  // мегабайта, так что первым нескольким постам откладывать нечего.
  const EAGER_POSTS = 3;
  let renderedPosts = 0;

  function newsPhotosHtml(item) {
    const photos = item.photos || [];
    if (!photos.length) return '';
    const eager = renderedPosts++ < EAGER_POSTS;
    const layout = photos.length === 1 ? 'one' : photos.length === 2 ? 'two' : photos.length === 3 ? 'three' : 'many';
    return '<div class="album album--' + layout + '">' +
      photos.slice(0, 4).map((photo, index) => {
        const rest = index === 3 && photos.length > 4 ? photos.length - 4 : 0;
        return '<button type="button" class="album__cell" data-photo="' + item.id + ':' + index + '">' +
          '<img src="' + photo.url + '" alt=""' + (eager ? '' : ' loading="lazy"') +
          ' data-retry="0" onerror="window.retryPhoto &amp;&amp; window.retryPhoto(this)">' +
          (rest ? '<span class="album__more">+' + rest + '</span>' : '') +
          '</button>';
      }).join('') + '</div>';
  }

  // Оборванный запрос (перезагрузка страницы, разрыв сети) оставлял пустое
  // место навсегда: тег <img> сам не повторяет. Пробуем ещё дважды.
  window.retryPhoto = function (img) {
    const tries = Number(img.dataset.retry || 0);
    if (tries >= 2) return;
    img.dataset.retry = String(tries + 1);
    const src = img.src;
    setTimeout(() => { img.src = ''; img.src = src; }, 400 * (tries + 1));
  };

  // «сегодня в 18:20» вместо голого «18:20»: в ленте, где посты идут за
  // несколько дней, одно время без дня ничего не говорит.
  function timeRu(iso) {
    if (!iso) return '';
    const d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso);
    const clock = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });

    const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const today = new Date();
    const midnight = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    const daysAgo = Math.round((midnight - day) / 86400000);

    if (daysAgo === 0) return 'сегодня в ' + clock;
    if (daysAgo === 1) return 'вчера в ' + clock;
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }) + ' в ' + clock;
  }

  function composeRowHtml(avatarUrl, name) {
    return '<button type="button" class="compose" id="addNews">' +
      avatarHtml(avatarUrl, name) +
      '<span class="compose__hint">Что нового?</span>' +
      '<svg class="compose__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' +
      '<rect x="3" y="5" width="18" height="14" rx="2"></rect><circle cx="9" cy="11" r="2"></circle>' +
      '<path d="M4 17l5-4 4 3 3-2 4 3"></path></svg>' +
      '</button>';
  }

  // Обвязка разделена: то, что живёт на всю ленту (счётчик просмотров), и то,
  // что принадлежит одному посту. Иначе перерисовка одного поста после лайка
  // навесила бы обработчики на остальные по второму разу — on() вешает их
  // прямо на узлы, а не делегированием.
  // В Telegram WebView клавиатуру нечем закрыть: тап по пустому месту не
  // снимает фокус сам, кнопки «Готово» над клавиатурой нет. Вешаем это один
  // раз на весь документ — касание мимо поля убирает фокус, как в обычном
  // приложении.
  let dismissKeyboardWired = false;
  function wireKeyboardDismiss() {
    if (dismissKeyboardWired) return;
    dismissKeyboardWired = true;
    document.addEventListener('touchstart', (event) => {
      const active = document.activeElement;
      if (!active || !/^(INPUT|TEXTAREA)$/.test(active.tagName)) return;
      if (event.target === active || event.target.closest('.modal')) return;
      active.blur();
    }, { passive: true });
  }

  function wireFeed(data) {
    wireKeyboardDismiss();
    document.querySelectorAll('.post[data-post]').forEach((node) => wirePost(node, data));

    // Просмотры считаем по факту появления поста на экране, пачкой — иначе
    // вышел бы запрос на каждый пост ленты.
    const pending = new Set();
    let timer = null;
    const flush = () => {
      timer = null;
      if (!pending.size) return;
      const ids = [...pending];
      pending.clear();
      api('/news/views', { method: 'POST', body: { post_ids: ids } }).catch(() => {});
    };
    const seen = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        pending.add(Number(entry.target.dataset.post));
        seen.unobserve(entry.target);
        if (timer === null) timer = setTimeout(flush, 1200);
      });
    }, { threshold: 0.4 });
    document.querySelectorAll('.post[data-post]').forEach((el) => seen.observe(el));
  }

  function wirePost(node, data) {
    const postId = node.dataset.post;
    const post = data.items.find((i) => String(i.id) === String(postId));

    on('[data-like]', 'click', () => sendReaction(postId, '❤️'), node);

    // Разворачиваем насовсем: свернуть обратно незачем — человек нажал,
    // потому что хочет дочитать, а не полистать туда-сюда.
    on('[data-more]', 'click', (event) => {
      const text = node.querySelector('.post__text');
      if (text) text.classList.remove('post__text--clamped');
      event.currentTarget.remove();
    }, node);

    on('[data-photo]', 'click', (event) => {
      const index = Number(event.currentTarget.dataset.photo.split(':')[1]);
      if (post) photoViewer(post.photos, index);
    }, node);

    on('[data-del-news]', 'click', () => {
      confirmAction('Удалить новость?', async () => {
        try {
          await api('/news/' + postId + feedScope(), { method: 'DELETE' });
          toast('Новость удалена');
          startRender(renderFeed);
        } catch (error) { fail(error); }
      });
    }, node);

    async function sendReaction(id, emoji) {
      try {
        const result = await api('/news/' + id + '/reactions', { method: 'POST', body: { emoji: emoji } });
        const target = data.items.find((i) => String(i.id) === String(id));
        if (!target) return;
        target.reactions = result.reactions;

        // Перерисовываем только саму кнопку лайка. Раньше заменялся весь пост
        // целиком — вместе с ним пересоздавались теги <img>, и фотографии
        // мигали при каждом нажатии.
        const button = node.querySelector('[data-like]');
        if (!button) return;
        const liked = (result.reactions || []).some((r) => r.emoji === '❤️' && r.mine);
        const count = (result.reactions || []).reduce((sum, r) => sum + r.count, 0);
        button.classList.toggle('act--on', liked);
        button.innerHTML = iconLike(liked) + '<span>' + (count || '') + '</span>';
      } catch (error) { fail(error); }
    }
  }

  // Чужой профиль. Сейчас сюда ниоткуда не переходят: из ленты убрали
  // и подпись-ссылку, и комментарии, а имя в бюро перестало быть кнопкой —
  // ходить друг к другу в карточки в приложении больше незачем. Экран
  // оставлен целым: понадобится — вернуть ссылку на него это одна строка.
  function openPerson(userId) {
    state.personId = userId;
    // Чужой профиль открываем с публикаций, а не с той вкладки, на которой
    // человек оставил свой: ради постов сюда и переходят.
    state.personBackTab = state.tab === 'person' ? state.personBackTab : state.tab;
    state.tab = 'person';
    renderTabs();
    renderTab();
  }

  function backFromPerson() {
    state.tab = state.personBackTab || 'home';
    state.personId = null;
    renderTabs();
    renderTab();
  }

  // --- Федеральное бюро ------------------------------------------------------
  // Один экран на всех: смотрят все, кроме корпорантов, а правят федеральный
  // координатор и админ — им же на этом экране показываются кнопки. Заводить
  // ради правки отдельный экран незачем.

  async function renderBureau(gen) {
    const data = await api('/bureau');
    const items = data.items || [];

    const rows = items.length
      ? items.map((item) =>
          '<div class="row">' +
          avatarHtml(item.avatar, item.name) +
          '<div class="row__main" style="margin-left:var(--space-12)">' +
          '<span class="bureau__name">' + esc(item.name) + '</span>' +
          '<div class="row__sub">' + esc(item.title) +
          (item.region ? ' · ' + esc(item.region) : '') + '</div></div>' +
          (data.can_edit
            ? '<span class="bureau__acts">' +
              '<button type="button" class="bureau__act" data-edit="' + item.id + '">Должность</button>' +
              '<button type="button" class="bureau__act bureau__act--drop" data-drop="' + item.id + '">Убрать</button></span>'
            : '') +
          '</div>').join('')
      : '<div class="empty">В бюро пока никого нет</div>';

    setView(
      '<div class="card"><div class="card__title">Федеральное бюро</div>' +
      '<div class="row__sub">Кураторствует регионы и ведёт то, что к региону не привязано.</div>' +
      '</div>' +
      '<div class="card" style="padding:0 var(--space-12)">' + rows + '</div>' +
      (data.can_edit
        ? '<div class="btn-row"><button class="btn btn--block" id="bureauAdd">Добавить человека</button></div>'
        : '')
    , gen);

    const add = document.getElementById('bureauAdd');
    if (add) add.onclick = () => personPicker(null, (memberId) => {
      closeModal();
      bureauTitleForm(null, '', memberId);
    }, false);

    on('[data-edit]', 'click', (event) => {
      const item = items.find((i) => String(i.id) === event.currentTarget.dataset.edit);
      if (item) bureauTitleForm(item.id, item.title, null);
    });

    on('[data-drop]', 'click', async (event) => {
      const item = items.find((i) => String(i.id) === event.currentTarget.dataset.drop);
      if (!item || !confirm('Убрать ' + item.name + ' из бюро?')) return;
      try {
        await api('/bureau/' + item.id, { method: 'DELETE' });
        toast('Убран из бюро');
        startRender(renderBureau);
      } catch (error) { fail(error); }
    });
  }

  // Одна форма и на добавление, и на смену должности: поля те же, отличается
  // только куда отправить.
  function bureauTitleForm(rowId, title, memberId) {
    modal(rowId ? 'Должность в бюро' : 'Новый человек в бюро',
      '<div class="field"><label>Должность</label>' +
      '<input id="bTitle" value="' + esc(title || '') + '" placeholder="Куратор Сибири" /></div>' +
      '<div class="btn-row"><button class="btn" id="bSave">Сохранить</button>' +
      '<button class="btn btn--ghost" id="bCancel">Отмена</button></div>',
      () => {
        document.getElementById('bCancel').onclick = closeModal;
        document.getElementById('bSave').onclick = async () => {
          const value = document.getElementById('bTitle').value.trim();
          if (value.length < 2) { toast('Укажите должность'); return; }
          try {
            if (rowId) {
              await api('/bureau/' + rowId, { method: 'PATCH', body: { title: value } });
            } else {
              await api('/bureau', { method: 'POST', body: { member_id: memberId, title: value } });
            }
            closeModal();
            toast('Сохранено');
            startRender(renderBureau);
          } catch (error) { fail(error); }
        };
      });
  }

  async function renderPersonPage(gen) {
    const data = await api('/profile/' + state.personId);

    // Написать можно всем и всегда — кнопка ни от какой настройки не зависит.
    // Единственное, что ей нужно, — ник, по которому открыть переписку. У тех,
    // кого руководитель завёл через «Состав», ника может не быть: тогда кнопка
    // не притворяется рабочей, а честно говорит почему.
    const write = data.telegram
      ? '<a class="btn btn--block" href="https://t.me/' +
        encodeURIComponent(String(data.telegram).replace(/^@/, '')) +
        '" target="_blank" rel="noopener">' + iconSend() + 'Написать в Телеграм</a>'
      : '<div class="btn btn--block btn--off">' + iconSend() + 'Телеграм не указан</div>';

    setView(
      '<button type="button" class="back-link" id="personBack">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"></path></svg>' +
      '<span>Назад</span></button>' +
      profileHeadHtml(data, data.name) +
      (data.is_me
        ? '<div class="btn-row"><button class="btn btn--block" id="personEdit">Редактировать</button></div>'
        : '<div class="btn-row">' + write + '</div>') +
      '<div id="profileTabBody" class="ptab-body"></div>'
    , gen);

    document.getElementById('profileTabBody').innerHTML = personAboutHtml(data);

    document.getElementById('personBack').onclick = backFromPerson;
    const edit = document.getElementById('personEdit');
    if (edit) edit.onclick = () => {
      state.profileEdit = true;
      state.tab = 'profile';
      state.personId = null;
      renderTabs();
      renderTab();
    };
  }

  function personAboutHtml(data) {
    // Скрытого телефона тут нет вовсе: не строка «скрыто», а её отсутствие —
    // скрытое не должно себя выдавать.
    const contacts =
      (data.telegram
        ? '<div class="row"><div class="row__main"><div class="row__title">Телеграм</div></div>' +
          '<div class="row__side">' + esc(data.telegram) + '</div></div>'
        : '') +
      (data.phone
        ? '<div class="row"><div class="row__main"><div class="row__title">Телефон</div></div>' +
          '<div class="row__side">' + esc(data.phone) + '</div></div>'
        : '');

    const html =
      (data.education && data.education.length
        ? section('Учёба', '<div class="card card--rows">' + rowsHtml(data.education) + '</div>')
        : '') +
      (contacts ? section('Контакты', '<div class="card card--rows">' + contacts + '</div>') : '');

    return html || '<div class="empty">Человек о себе пока ничего не рассказал</div>';
  }

  function iconSend() {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" class="btn__icon"><path d="M21 4L3 11l6 2 2 6z"></path><path d="M21 4l-10 9"></path></svg>';
  }

  // Полноэкранный просмотр с листанием: свайп пальцем, стрелки с клавиатуры и
  // Escape на закрытие. Лента — один ряд с translateX, как в Telegram, чтобы
  // соседний кадр «выезжал» вместе с пальцем, а не появлялся рывком.
  function photoViewer(photos, startIndex) {
    if (!photos || !photos.length) return;
    let index = Math.min(Math.max(startIndex || 0, 0), photos.length - 1);

    const root = document.createElement('div');
    root.className = 'viewer';
    root.innerHTML =
      '<div class="viewer__bar">' +
      '<span class="viewer__counter"></span>' +
      '<button type="button" class="viewer__close" aria-label="Закрыть">×</button></div>' +
      '<div class="viewer__track">' +
      photos.map((photo) => '<div class="viewer__slide"><img src="' + photo.url + '" alt=""></div>').join('') +
      '</div>';
    document.body.appendChild(root);
    document.body.classList.add('viewer-open');

    const track = root.querySelector('.viewer__track');
    const counter = root.querySelector('.viewer__counter');

    function draw(offset, animate) {
      track.style.transition = animate ? 'transform .25s ease' : 'none';
      track.style.transform = 'translateX(calc(' + (-index * 100) + '% + ' + (offset || 0) + 'px))';
      counter.textContent = (index + 1) + ' / ' + photos.length;
    }
    function go(step) {
      index = Math.min(Math.max(index + step, 0), photos.length - 1);
      draw(0, true);
    }
    function close() {
      document.removeEventListener('keydown', onKey);
      document.body.classList.remove('viewer-open');
      root.remove();
    }
    function onKey(event) {
      if (event.key === 'Escape') close();
      else if (event.key === 'ArrowRight') go(1);
      else if (event.key === 'ArrowLeft') go(-1);
    }

    let startX = null;
    track.addEventListener('touchstart', (event) => { startX = event.touches[0].clientX; }, { passive: true });
    track.addEventListener('touchmove', (event) => {
      if (startX === null) return;
      draw(event.touches[0].clientX - startX, false);
    }, { passive: true });
    track.addEventListener('touchend', (event) => {
      if (startX === null) return;
      const delta = event.changedTouches[0].clientX - startX;
      startX = null;
      // Порог примерно в четверть экрана — иначе случайное касание листает.
      if (Math.abs(delta) > root.clientWidth / 4) go(delta < 0 ? 1 : -1);
      else draw(0, true);
    });

    root.querySelector('.viewer__close').onclick = close;
    root.addEventListener('click', (event) => { if (event.target === root) close(); });
    document.addEventListener('keydown', onKey);
    draw(0, false);
  }

  async function newsForm() {
    // Подпись всегда официальная: в ленте говорит ячейка, отделение или
    // Братство. Личных записей в ней больше нет, поэтому и выбирать не из
    // чего — прежняя развилка «из личного кабинета от себя, из управления от
    // отделения» исчезла вместе с личными постами.
    const info = await api('/news/byline?official=true');
    const limit = info.photo_limit || 10;

    // Свой набор файлов вместо того, что держит поле выбора: из него нельзя
    // убрать одну лишнюю картинку — список файлов у поля только для чтения,
    // и раньше передумавшему приходилось выбирать всё заново.
    const chosen = [];
    const previews = new Map();

    modal('Новость',
      // Подпись показываем так, как она будет выглядеть в ленте, — с аватаром.
      // И сразу говорим, кого потревожит уведомление: раньше об этом узнавали
      // уже после публикации.
      '<div class="compose-as">' + avatarHtml(info.avatar, info.byline) +
      '<span class="compose-as__text"><span class="compose-as__name">' + esc(info.byline) + '</span>' +
      (info.audience ? '<span class="row__sub">' + esc(info.audience) + '</span>' : '') +
      '</span></div>' +
      '<textarea id="newsText" class="compose-text" placeholder="Что нового?"></textarea>' +
      '<div class="compose-sep"></div>' +
      '<input type="file" id="newsPhotos" accept="image/*" multiple hidden />' +
      '<div id="newsAlbum"></div>' +
      '<div class="btn-row compose-actions">' +
      '<button class="btn" id="newsSave">Опубликовать</button>' +
      '<button class="btn btn--ghost" id="newsCancel">Отмена</button></div>',
      () => {
        const picker = document.getElementById('newsPhotos');
        const album = document.getElementById('newsAlbum');

        const drawAlbum = () => {
          if (!chosen.length) {
            album.innerHTML =
              '<button type="button" class="attach" id="attachEmpty">' +
              '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="attach__icon"><rect x="3" y="5" width="18" height="14" rx="2"></rect><path d="M3 16l4.5-4.5 3.5 3.5 3-3L21 17"></path><circle cx="8.5" cy="9.5" r="1.3"></circle></svg>' +
              '<span class="attach__title">Добавить фотографии</span>' +
              '<span class="attach__hint">Можно несколько сразу</span></button>';
          } else {
            album.innerHTML =
              '<div class="thumbs">' +
              chosen.map((file, index) =>
                '<span class="thumb"><img src="' + previews.get(file) + '" alt="">' +
                '<button type="button" class="thumb__drop" data-drop-photo="' + index + '" aria-label="Убрать">' +
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"></path></svg>' +
                '</button></span>').join('') +
              (chosen.length < limit
                ? '<button type="button" class="thumb thumb--add" id="attachMore" aria-label="Добавить ещё">' +
                  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M12 5v14M5 12h14"></path></svg></button>'
                : '') +
              '</div>' +
              '<div class="row__sub compose-count">' + photoCount(chosen.length) +
              (chosen.length >= limit ? ' · это предел' : '') + '</div>';
          }
          on('#attachEmpty, #attachMore', 'click', () => picker.click(), album);
          on('[data-drop-photo]', 'click', (event) => {
            const index = Number(event.currentTarget.dataset.dropPhoto);
            const [gone] = chosen.splice(index, 1);
            URL.revokeObjectURL(previews.get(gone));
            previews.delete(gone);
            drawAlbum();
          }, album);
        };

        picker.onchange = () => {
          for (const file of picker.files) {
            if (chosen.length >= limit) { toast('Больше ' + limit + ' фотографий за раз нельзя'); break; }
            chosen.push(file);
            previews.set(file, URL.createObjectURL(file));
          }
          // Обнуляем поле: иначе повторный выбор того же файла не вызовет
          // событие, и человек решит, что кнопка не работает.
          picker.value = '';
          drawAlbum();
        };
        drawAlbum();

        onModalClose(() => previews.forEach((url) => URL.revokeObjectURL(url)));
        document.getElementById('newsCancel').onclick = closeModal;

        submitOnce(document.getElementById('newsSave'), 'Публикую…', async () => {
          const text = document.getElementById('newsText').value.trim();
          if (!text) { toast('Введите текст'); return; }
          // multipart — текст и файлы одним запросом (api/routers/news.py::publish)
          const form = new FormData();
          form.append('text', text);
          form.append('official', 'true');
          // Кабинет передаём и здесь: сервер отказывает личному, а не только
          // приложение прячет кнопку.
          form.append('personal', state.cabinetMode === 'personal' ? 'true' : 'false');
          for (const file of chosen) form.append('files', file);
          try {
            await api('/news', { method: 'POST', body: form });
            closeModal();
            toast('Опубликовано');
            startRender(renderNews);
          } catch (error) { fail(error); }
        });
      });
  }

  function photoCount(n) {
    const tail = n % 100 >= 11 && n % 100 <= 14 ? 0 : n % 10;
    const word = tail === 1 ? 'фотография' : (tail >= 2 && tail <= 4 ? 'фотографии' : 'фотографий');
    return n + ' ' + word;
  }

  // --- Регистрация (?view=register) ------------------------------------------
  // Отдельный самодостаточный экран без топбара/вкладок — для человека, у
  // которого ещё нет аккаунта в системе (см. api/routers/register.py,
  // handlers/start.py). prefillRegionId — переход по APPLY_-ссылке региона,
  // пропускает шаг выбора отделения.

  async function renderRegisterView(gen, prefillRegionId) {
    const data = await api('/register/context');
    if (data.already_registered) {
      setView('<div class="empty">Личный кабинет уже создан — открываю…</div>', gen);
      location.href = '/';
      return;
    }

    const regions = data.regions || [];
    setView(
      '<div class="card"><div class="card__title">Регистрация</div>' +
      '<div class="field"><label>ФИО</label><input id="rName" placeholder="Иванов Иван Иванович" /></div>' +
      '<div class="field"><label>Дата рождения</label><input id="rBirth" placeholder="20.02.2000" inputmode="numeric" /></div>' +
      '<div class="field"><label>Телефон</label><input id="rPhone" /></div>' +
      '<div class="field"><label>Ник в Telegram</label><input id="rTelegram" placeholder="@qwerty" /></div>' +
      '<div class="field"><label>Отделение</label><select id="rRegion"><option value="">Выберите отделение</option>' +
      regions.map((r) => '<option value="' + r.id + '">' + esc(r.label) + '</option>').join('') +
      '</select></div>' +
      '<div class="field"><label id="rUniversityLabel">ВУЗ</label><input id="rUniversityInput" autocomplete="off" disabled />' +
      '<div class="row__sub" style="margin-top:var(--space-4)">Если вуза нет в списке, напишите его полное название — он будет добавлен вместе с заявкой.</div>' +
      '<div id="rUniversitySuggestions" class="chips" style="margin-top:var(--space-8)"></div></div>' +
      '<div class="field"><label id="rFacultyLabel">Факультет</label><input id="rFaculty" /></div>' +
      '<div class="field"><label>Курс</label><select id="rCourse">' +
      '<option value="">Выберите курс</option>' +
      [1, 2, 3, 4, 5, 6].map((n) => '<option value="' + n + '">' + n + '</option>').join('') +
      '<option value="graduated">Окончил</option>' +
      '</select></div>' +
      '<div class="field" id="rWorkplaceField" hidden><label>Место работы</label><input id="rWorkplace" /></div>' +
      '<div class="field" id="rEducationLevelField"><label>Уровень обучения</label><select id="rEducationLevel">' +
      '<option value="">Выберите уровень</option>' +
      '<option value="bachelor">Бакалавриат</option>' +
      '<option value="master">Магистратура</option>' +
      '<option value="postgraduate">Аспирантура</option>' +
      '<option value="residency">Ординатура</option>' +
      '</select></div>' +
      '<div class="field"><label>Статус</label><select id="rStatus">' +
      '<option value="">Выберите статус</option>' +
      '<option value="activist">Корпорант</option>' +
      '<option value="member">Член Братства</option>' +
      '<option value="alumni">Выпускник</option>' +
      '</select></div>' +
      '<button class="btn btn--block" id="rSubmit">Отправить</button>' +
      '</div>'
    , gen);

    applyPhoneMask(document.getElementById('rPhone'));
    applyDateMask(document.getElementById('rBirth'));

    const courseSelect = document.getElementById('rCourse');
    const workplaceField = document.getElementById('rWorkplaceField');
    const educationLevelField = document.getElementById('rEducationLevelField');
    const uniLabel = document.getElementById('rUniversityLabel');
    const facultyLabel = document.getElementById('rFacultyLabel');
    courseSelect.addEventListener('change', () => {
      const graduated = courseSelect.value === 'graduated';
      workplaceField.hidden = !graduated;
      // Окончил — уровень обучения больше не актуален, поле убираем совсем.
      educationLevelField.hidden = graduated;
      if (graduated) document.getElementById('rEducationLevel').value = '';
      // Вуз и факультет спрашиваем и у выпускника, но в прошедшем времени:
      // иначе «ВУЗ» рядом с «Окончил» читается как вопрос про сейчас, и
      // человек не понимает, что писать.
      uniLabel.textContent = graduated ? 'Где учились' : 'ВУЗ';
      facultyLabel.textContent = graduated ? 'Какой факультет окончили' : 'Факультет';
    });

    const regionSelect = document.getElementById('rRegion');
    const uniInput = document.getElementById('rUniversityInput');
    const uniSuggestions = document.getElementById('rUniversitySuggestions');
    let selectedUniversityId = null;
    let uniTimer;

    function resetUniversity() {
      selectedUniversityId = null;
      uniInput.value = '';
      uniSuggestions.innerHTML = '';
      uniInput.disabled = !regionSelect.value;
      uniInput.placeholder = regionSelect.value ? 'Начните вводить название' : 'Сначала выберите отделение';
    }

    if (prefillRegionId && regions.some((r) => r.id === prefillRegionId)) {
      regionSelect.value = String(prefillRegionId);
    }
    resetUniversity();
    regionSelect.addEventListener('change', resetUniversity);

    uniInput.addEventListener('input', () => {
      selectedUniversityId = null;
      clearTimeout(uniTimer);
      const q = uniInput.value.trim();
      const regionId = regionSelect.value;
      if (!q || !regionId) { uniSuggestions.innerHTML = ''; return; }
      uniTimer = setTimeout(async () => {
        try {
          const res = await api('/register/universities?region_id=' + regionId + '&q=' + encodeURIComponent(q));
          uniSuggestions.innerHTML = res.items.map((u) =>
            '<button type="button" class="chip" data-uni="' + u.id + '" data-name="' + esc(u.name) + '">' + esc(u.name) + '</button>'
          ).join('');
          on('[data-uni]', 'click', (event) => {
            selectedUniversityId = Number(event.currentTarget.dataset.uni);
            uniInput.value = event.currentTarget.dataset.name;
            uniSuggestions.innerHTML = '';
          }, uniSuggestions);
        } catch (error) { /* автодополнение необязательно — молча пропускаем сбой */ }
      }, 300);
    });

    document.getElementById('rSubmit').onclick = async () => {
      // Все поля обязательны — эта анкета уже упрощённая версия отбора на
      // сайте, сокращать её дальше не нужно.
      const regionId = regionSelect.value ? Number(regionSelect.value) : null;
      if (!regionId) { toast('Выберите отделение'); return; }
      const fullName = document.getElementById('rName').value.trim();
      if (fullName.length < 2) { toast('Введите ФИО'); return; }
      const birthRaw = document.getElementById('rBirth').value.trim();
      if (!/^\d{2}\.\d{2}\.\d{4}$/.test(birthRaw)) { toast('Введите дату рождения полностью — ДД.ММ.ГГГГ'); return; }
      const phone = document.getElementById('rPhone').value.trim();
      if (!phone) { toast('Введите телефон'); return; }
      let telegramUsername = document.getElementById('rTelegram').value.trim();
      if (telegramUsername && !telegramUsername.startsWith('@')) telegramUsername = '@' + telegramUsername;
      if (!/^@[A-Za-z][A-Za-z0-9_]{2,31}$/.test(telegramUsername)) {
        toast('Ник в Telegram — латиницей, формат «@qwerty»'); return;
      }
      const uniName = uniInput.value.trim();
      if (!uniName) { toast('Введите ВУЗ'); return; }
      const uniProblem = universityNameProblem(uniName);
      if (uniProblem) { toast(uniProblem); return; }
      const faculty = document.getElementById('rFaculty').value.trim();
      if (!faculty) { toast('Введите факультет'); return; }
      const courseRaw = document.getElementById('rCourse').value;
      if (!courseRaw) { toast('Выберите курс'); return; }
      const graduatedUniversity = courseRaw === 'graduated';
      const workplace = document.getElementById('rWorkplace').value.trim();
      if (graduatedUniversity && !workplace) { toast('Укажите место работы'); return; }
      const educationLevel = document.getElementById('rEducationLevel').value;
      if (!graduatedUniversity && !educationLevel) { toast('Выберите уровень обучения'); return; }
      const status = document.getElementById('rStatus').value;
      if (!status) { toast('Выберите статус'); return; }

      const universityId = selectedUniversityId;

      try {
        await api('/register/submit', {
          method: 'POST',
          body: {
            full_name: fullName,
            birth_date: birthRaw,
            phone: phone,
            telegram_username: telegramUsername,
            region_id: regionId,
            university_id: universityId,
            university_name: uniName,
            faculty: faculty,
            course: graduatedUniversity ? null : Number(courseRaw),
            graduated_university: graduatedUniversity,
            workplace: graduatedUniversity ? workplace : null,
            education_level: graduatedUniversity ? null : educationLevel,
            status: status,
          },
        });
        setView('<div class="empty">✅ Анкета отправлена — ждите подтверждения. Можно закрыть это окно.</div>', gen);
      } catch (error) { fail(error); }
    };
  }

  // --- Запуск ---------------------------------------------------------------

  function renderImpersonationBanner() {
    const banner = document.getElementById('impersonationBanner');
    if (state.me && state.me.impersonated_by) {
      banner.hidden = false;
      document.getElementById('impersonationName').textContent = state.me.full_name;
      document.getElementById('impersonationExit').onclick = async () => {
        try {
          await api('/me/stop-impersonation', { method: 'POST' });
          toast('Вернулись в свой аккаунт');
          location.reload();
        } catch (error) { fail(error); }
      };
    } else {
      banner.hidden = true;
    }
  }

  async function refreshMe() {
    state.me = await api('/me');
    renderImpersonationBanner();
    renderCabinetSwitch();
    renderTabs();
    return state.me;
  }

  async function boot() {
    const introEl = document.getElementById('intro');
    const params = new URLSearchParams(location.search);
    if (params.get('view') === 'register') {
      // Незнакомый telegram_id — своя, самодостаточная форма без топбара/
      // вкладок (см. handlers/start.py, api/routers/register.py). Имени тут
      // ещё нет, приветствовать некого — прячем заставку сразу.
      if (introEl) introEl.classList.add('intro--hidden');
      const topbar = document.querySelector('.topbar');
      if (topbar) topbar.hidden = true;
      const regionParam = params.get('region_id');
      startRender((gen) => renderRegisterView(gen, regionParam ? Number(regionParam) : null));
      return;
    }

    try {
      state.me = await api('/me');
    } catch (error) {
      // Кабинет иногда не открывается по кнопке — по разовому сбою сети или
      // проверки подписи. Раньше здесь оставалась одна строка текста, и
      // единственным выходом было закрыть окно и открыть заново. Теперь
      // попытку можно повторить, не выходя.
      if (introEl) introEl.classList.add('intro--hidden');
      setView(
        '<div class="notice notice--warn">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 8v5M12 16v.01"></path></svg>' +
        '<div><div>Кабинет не открылся: ' + esc(error.message) + '</div>' +
        '<button class="btn btn--small" id="bootRetry">Попробовать снова</button></div></div>');
      const retry = document.getElementById('bootRetry');
      if (retry) retry.onclick = () => { setView('<div class="loader">Загружаю…</div>'); boot().catch(fail); };
      return;
    }

    showIntroGreeting(state.me.full_name);

    document.getElementById('userName').textContent = state.me.full_name;
    // Подпись под именем — только про управление. Статус члена Братства оттуда
    // убран: рядом с именем в профиле его показывает герб, а в шапке он висел
    // на всех вкладках и дублировал сам себя.
    state.regionId = state.me.regions.length ? state.me.regions[0].id : null;
    // Участнику без управленческих регионов переключаться не на что — сразу
    // в личный кабинет. У federal/coordinator/superuser — свой собственный
    // кабинет по умолчанию (аналитика по всем регионам и т.п.), а не первый
    // попавшийся регион. У leader/cell_leader — как и раньше, сразу в их
    // единственный регион.
    // Открывается всегда личный кабинет — у всех, независимо от должности.
    // Раньше руководителя встречала сводка его отделения, а федерального —
    // аналитика: приложение начиналось с работы, а не с человека. В кабинет
    // управления теперь заходят сами, переключателем в шапке.
    const roleLine = document.getElementById('userRole');
    if (state.me.has_personal_cabinet) {
      state.cabinetMode = 'personal';
      state.tab = 'home';
      // Подпись под именем в личном кабинете — статус членства, не должность
      // (её ставит switchCabinet при переходе в управление).
      roleLine.textContent = '';
    } else if (hasOwnCabinet()) {
      // Сюда попадает только первый superuser на пустой системе: он заводится
      // до того, как появилось хоть одно отделение (utils/users.py::
      // ensure_superuser), и карточки в составе у него ещё нет физически —
      // класть её некуда. Как только отделения появились, ему заводят карточку,
      // и он открывается личным кабинетом, как все. На боевой системе таких
      // сейчас ноль.
      state.cabinetMode = 'own';
      state.tab = 'analytics';
      roleLine.textContent = state.me.role_label || '';
    } else {
      state.cabinetMode = 'region';
      state.tab = 'dashboard';
      roleLine.textContent = state.me.role_label || '';
    }

    renderImpersonationBanner();
    renderCabinetSwitch();
    renderTabs();
    renderTab();
  }

  boot();
})();
