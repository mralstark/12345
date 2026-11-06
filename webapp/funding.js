/* Monthly funding shares one ledger between regional and coordinating cabinets. */
(function () {
  'use strict';

  const MONTH = /^(20\d{2}|2100)-(0[1-9]|1[0-2])$/;

  function parseRubles(value, allowZero) {
    const raw = String(value).trim().replace(/[ \u00a0\u202f]/g, '');
    const match = /^(0|[1-9]\d{0,7})(?:[,.](\d{1,2}))?$/.exec(raw);
    if (!match) throw new Error('Введите сумму в рублях, например 1000 или 1000,50');
    const amount = Number(match[1]) * 100 + Number((match[2] || '').padEnd(2, '0'));
    if (!Number.isSafeInteger(amount) || amount > 2147483647 || (!allowZero && amount <= 0)) {
      throw new Error('Сумма должна быть ' + (allowZero ? 'от 0' : 'больше 0') + ' до 21 474 836,47 ₽');
    }
    return amount;
  }

  function rubles(amount) {
    return Math.trunc(amount / 100) + (amount % 100 ? ',' + String(amount % 100).padStart(2, '0') : '');
  }

  function requestId() {
    if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
  }

  function monthEnd(month) {
    const [year, number] = month.split('-').map(Number);
    return new Date(Date.UTC(year, number, 0)).toISOString().slice(0, 10);
  }

  function summary(row, ctx) {
    const { esc, money } = ctx;
    const over = row.remaining !== null && row.remaining < 0;
    return '<dl class="funding-numbers">' +
      '<div><dt>План</dt><dd>' + (row.plan ? money(row.plan.amount) : '<span class="funding-unset">Не задан</span>') + '</dd></div>' +
      '<div><dt>Выдано</dt><dd>' + money(row.issued) + '</dd></div>' +
      '<div' + (over ? ' class="funding-overrun"' : '') + '><dt>' + (over ? 'Сверх плана' : 'Осталось выдать') + '</dt><dd>' +
      (row.remaining === null ? '—' : money(Math.abs(row.remaining))) + '</dd></div></dl>' +
      (row.plan && row.plan.note ? '<p class="funding-note">' + esc(row.plan.note) + '</p>' : '');
  }

  async function render(ctx) {
    const { api, state, gen, setView, esc, money, modal, closeModal, submitOnce, on, fail, toast, dateRu, todayIsoLocal } = ctx;
    const own = state.cabinetMode === 'own';
    let selected = own ? (state.fundingFilter || 'all') : String(ctx.regionId);
    const allowed = state.me.regions || [];
    if (selected !== 'all' && !allowed.some(r => String(r.id) === selected)) selected = own ? 'all' : String(ctx.regionId);
    const scope = selected === 'all' ? '' : '&region_id=' + encodeURIComponent(selected);
    const requestedMonth = state.fundingMonth && MONTH.test(state.fundingMonth) ? state.fundingMonth : '';
    const data = await api('/funding?' + (requestedMonth ? 'month=' + requestedMonth : '') + scope);
    state.fundingMonth = data.month;
    const month = data.month;
    const query = 'month=' + month + scope;
    const scopeKey = (own ? 'own:' : 'region:') + selected;
    if (state.fundingScopeKey !== scopeKey) { state.fundingScopeKey = scopeKey; state.fundingSkip = 0; }
    const skip = Number.isInteger(state.fundingSkip) && state.fundingSkip >= 0 ? state.fundingSkip : 0;
    const ledger = await api('/funding/entries?' + query + '&limit=30&skip=' + skip);
    const byId = new Map(data.items.filter(r => r.plan).map(r => [String(r.plan.id), r]));
    const refresh = () => ctx.refresh();
    const rows = data.items.map((row, index) => {
      const actions = (row.can_manage ? '<button class="btn btn--ghost btn--small" data-funding-plan="' + index + '">' + (row.plan ? 'Изменить план' : 'Задать план') + '</button>' +
        (row.plan ? '<button class="btn btn--small" data-funding-issue="' + index + '"' + (month > todayIsoLocal().slice(0, 7) ? ' disabled' : '') + '>Записать выдачу</button>' : '') : '') +
        (row.plan ? '<button class="btn btn--ghost btn--small" data-funding-history="' + row.plan.id + '">История плана</button>' : '');
      return '<article class="card funding-account' + (row.region_id === null ? ' funding-account--department' : '') + '">' +
        '<div class="funding-account__heading"><h2>' + esc(row.name) + '</h2>' + (row.region_id === null ? '<span class="badge">Общий бюджет отдела</span>' : '') + '</div>' +
        summary(row, ctx) + (actions ? '<div class="btn-row funding-actions">' + actions + '</div>' : '') + '</article>';
    }).join('');
    const entries = ledger.items.map(entry => '<article class="funding-entry' + (entry.kind === 'cancel' || entry.cancelled ? ' funding-entry--cancelled' : '') + '">' +
      '<div class="funding-entry__top"><div><div class="row__title">' + esc(entry.region_name) + '</div>' +
      '<div class="row__sub">' + dateRu(entry.date) + ' · № ' + entry.id + '</div></div>' +
      '<div class="funding-entry__amount">' + (entry.kind === 'cancel' ? '−' : '') + money(entry.amount) + '</div></div>' +
      '<p class="funding-entry__comment">' + esc(entry.comment) + '</p>' +
      '<div class="funding-entry__bottom"><span class="row__sub">' + esc(entry.actor) + '</span>' +
      (entry.kind === 'cancel' ? '<span class="badge">Отмена записи № ' + entry.cancels_id + '</span>' : entry.cancelled ? '<span class="badge">Отменена</span>' : '') +
      (entry.can_cancel ? '<button class="btn btn--ghost btn--small" data-funding-cancel="' + entry.id + '">Отменить запись</button>' : '') + '</div></article>').join('');
    setView('<header class="screen-heading"><h1>Финансирование</h1></header><div class="funding-intro"><p>Месячные планы и учёт выданного финансирования. Эти записи не добавляют доходы или расходы в баланс региона.</p></div>' +
      '<div class="funding-toolbar"><div class="field"><label for="fundingMonth">Месяц плана</label><input type="month" id="fundingMonth" min="2000-01" max="2100-12" value="' + esc(month) + '" /></div>' +
      (own ? '<div class="field"><label for="fundingScope">Область</label><select id="fundingScope"><option value="all">Все доступные регионы и отдел</option>' + allowed.map(r => '<option value="' + r.id + '"' + (selected === String(r.id) ? ' selected' : '') + '>' + esc(r.name) + '</option>').join('') + '</select></div>' : '') +
      '<button class="btn btn--ghost" id="fundingExport">Скачать Excel</button></div>' +
      '<div class="funding-totals"><div><span>Всего запланировано</span><strong>' + (data.totals.without_plan === data.items.length ? '—' : money(data.totals.planned)) + '</strong></div>' +
      '<div><span>Всего выдано</span><strong>' + money(data.totals.issued) + '</strong></div>' +
      '<div><span>' + (data.totals.remaining < 0 ? 'Всего сверх плана' : 'Осталось выдать') + '</span><strong>' + (data.totals.without_plan === data.items.length ? '—' : money(Math.abs(data.totals.remaining))) + '</strong></div></div>' +
      (data.totals.without_plan ? '<p class="row__sub">Бюджетов без плана: ' + data.totals.without_plan + '. Они не включены в плановую сумму.</p>' : '') +
      '<div id="fundingExportResult" aria-live="polite"></div><div class="funding-accounts">' + rows + '</div>' +
      '<section class="card funding-ledger"><div class="card__title">Журнал выдач</div><p class="row__sub">Выдачи относятся к выбранному месяцу плана. Отмена сохраняет исходную запись и причину исправления.</p>' +
      (entries || '<div class="empty">Выдач за этот месяц пока нет</div>') +
      (skip || ledger.has_more ? '<div class="btn-row funding-pagination"><button class="btn btn--ghost" id="fundingPrev"' + (skip ? '' : ' disabled') + '>Предыдущие</button><span class="row__sub">Страница ' + (Math.floor(skip / 30) + 1) + '</span><button class="btn btn--ghost" id="fundingNext"' + (ledger.has_more ? '' : ' disabled') + '>Следующие</button></div>' : '') + '</section>', gen);

    document.getElementById('fundingMonth').onchange = event => {
      if (!MONTH.test(event.currentTarget.value)) { toast('Выберите месяц от января 2000 до декабря 2100'); return; }
      state.fundingMonth = event.currentTarget.value; state.fundingSkip = 0; refresh();
    };
    const filter = document.getElementById('fundingScope');
    if (filter) filter.onchange = event => { state.fundingFilter = event.currentTarget.value; state.fundingSkip = 0; refresh(); };
    const prev = document.getElementById('fundingPrev'), next = document.getElementById('fundingNext');
    if (prev) prev.onclick = () => { state.fundingSkip = Math.max(0, skip - 30); refresh(); };
    if (next) next.onclick = () => { state.fundingSkip = skip + 30; refresh(); };
    submitOnce(document.getElementById('fundingExport'), 'Готовлю Excel…', async () => {
      const result = document.getElementById('fundingExportResult');
      try {
        const file = await api('/funding/export?' + query, { method: 'POST' });
        if (!result.isConnected) return;
        result.innerHTML = '<p>Файл готов. Ссылка действует 10 минут.</p><a class="btn" id="fundingDownload" download="' + esc(file.filename) + '" href="' + esc(new URL(file.url, location.origin).href) + '">Скачать файл</a>';
        ctx.wireDownloadLink(document.getElementById('fundingDownload'), file);
      } catch (error) { if (result.isConnected) result.textContent = error.message; fail(error); }
    });
    on('[data-funding-plan]', 'click', event => planForm(data.items[Number(event.currentTarget.dataset.fundingPlan)]));
    on('[data-funding-issue]', 'click', event => issueForm(data.items[Number(event.currentTarget.dataset.fundingIssue)]));
    on('[data-funding-cancel]', 'click', event => cancelForm(ledger.items.find(e => e.id === Number(event.currentTarget.dataset.fundingCancel))));
    on('[data-funding-history]', 'click', event => history(Number(event.currentTarget.dataset.fundingHistory), 0));

    function formError(message) {
      const node = document.getElementById('fundingFormError');
      if (node) node.textContent = message;
    }

    function planForm(row) {
      modal((row.plan ? 'Изменить план' : 'План финансирования') + ': ' + row.name,
        '<p class="row__sub">' + esc(month) + '. Нулевой план означает, что финансирование не предусмотрено.</p>' +
        '<div class="field"><label for="fundingPlanAmount">Плановая сумма, ₽</label><input id="fundingPlanAmount" inputmode="decimal" maxlength="20" value="' + (row.plan ? esc(rubles(row.plan.amount)) : '') + '" placeholder="10000" /></div>' +
        '<div class="field"><label for="fundingPlanNote">Основание или комментарий</label><textarea id="fundingPlanNote" maxlength="1000" rows="3">' + esc(row.plan ? row.plan.note : '') + '</textarea></div>' +
        '<p class="funding-form-error" id="fundingFormError" role="status" aria-live="polite"></p>' +
        '<div class="btn-row"><button class="btn" id="fundingPlanSave">Сохранить план</button><button class="btn btn--ghost" id="fundingFormClose">Отмена</button></div>', () => {
          document.getElementById('fundingFormClose').onclick = closeModal;
          submitOnce(document.getElementById('fundingPlanSave'), 'Сохраняю…', async () => {
            try {
              const amount = parseRubles(document.getElementById('fundingPlanAmount').value, true);
              await api('/funding/plan', { method: 'PUT', body: { month, region_id: row.region_id, amount, note: document.getElementById('fundingPlanNote').value.trim(), revision: row.plan ? row.plan.revision : 0 } });
              closeModal(); toast('План сохранён'); refresh();
            } catch (error) { formError(error.message); }
          });
        });
    }

    function issueForm(row) {
      // Retain the same key for every retry in this open form, including a timeout.
      // Changing an already-used request cannot silently create a second issuance.
      const key = requestId();
      const today = todayIsoLocal(), last = monthEnd(month), maximum = today < last ? today : last;
      modal('Записать выдачу: ' + row.name,
        '<p>План: ' + money(row.plan.amount) + '. Осталось выдать: ' + money(Math.max(0, row.remaining)) + '.</p>' +
        '<div class="field"><label for="fundingIssueAmount">Выданная сумма, ₽</label><input id="fundingIssueAmount" inputmode="decimal" maxlength="20" placeholder="1000" /></div>' +
        '<div class="field"><label for="fundingIssueDate">Дата фактической выдачи</label><input id="fundingIssueDate" type="date" min="' + month + '-01" max="' + maximum + '" value="' + (today.startsWith(month) ? today : month + '-01') + '" /></div>' +
        '<div class="field"><label for="fundingIssueComment">Назначение выдачи</label><textarea id="fundingIssueComment" minlength="3" maxlength="1000" rows="3" placeholder="Например, организация мероприятия"></textarea></div>' +
        '<div id="fundingOverPlan" class="funding-warning" hidden><p>Эта выдача превысит план. Убедитесь, что деньги действительно выданы.</p><label class="funding-checkbox"><input type="checkbox" id="fundingAllowOver" /><span>Записать выдачу сверх плана</span></label></div>' +
        '<p class="row__sub">Запись фиксирует выдачу денег. Перевод денег и доход в балансе региона автоматически не создаются.</p>' +
        '<p class="funding-form-error" id="fundingFormError" role="status" aria-live="polite"></p>' +
        '<div class="btn-row"><button class="btn" id="fundingIssueSave">Записать выдачу</button><button class="btn btn--ghost" id="fundingFormClose">Отмена</button></div>', () => {
          document.getElementById('fundingFormClose').onclick = closeModal;
          const warning = document.getElementById('fundingOverPlan');
          document.getElementById('fundingIssueAmount').oninput = event => {
            try { warning.hidden = parseRubles(event.currentTarget.value, false) <= row.remaining; }
            catch (_) { warning.hidden = true; }
          };
          submitOnce(document.getElementById('fundingIssueSave'), 'Записываю…', async () => {
            try {
              const amount = parseRubles(document.getElementById('fundingIssueAmount').value, false);
              const comment = document.getElementById('fundingIssueComment').value.trim();
              const date = document.getElementById('fundingIssueDate').value;
              const allow = document.getElementById('fundingAllowOver').checked;
              if (comment.length < 3) throw new Error('Укажите назначение выдачи — не короче трёх символов');
              if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !date.startsWith(month + '-') || date > maximum) throw new Error('Выберите прошедшую или сегодняшнюю дату в месяце плана');
              if (amount > row.remaining && !allow) { warning.hidden = false; throw new Error('Подтвердите запись сверх плана'); }
              await api('/funding/entries', { method: 'POST', body: { plan_id: row.plan.id, amount, date, comment, request_id: key, allow_over_plan: allow } });
              closeModal(); toast('Выдача записана'); refresh();
            } catch (error) {
              if (/превысит|сверх плана/.test(error.message)) warning.hidden = false;
              formError(error.message);
            }
          });
        });
    }

    function cancelForm(entry) {
      if (!entry) return;
      const key = requestId();
      modal('Отменить запись о выдаче', '<p>' + esc(entry.region_name) + ' · ' + money(entry.amount) + ' · ' + dateRu(entry.date) + '</p>' +
        '<p>Исходная запись останется в истории, а сумма будет исключена из «Выдано». Это исправление реестра, а не возврат денег.</p>' +
        '<div class="field"><label for="fundingCancelReason">Причина отмены</label><textarea id="fundingCancelReason" minlength="5" maxlength="1000" rows="3" placeholder="Например, сумма внесена ошибочно"></textarea></div>' +
        '<p class="funding-form-error" id="fundingFormError" role="status" aria-live="polite"></p>' +
        '<div class="btn-row"><button class="btn btn--danger" id="fundingCancelSave">Отменить запись</button><button class="btn btn--ghost" id="fundingFormClose">Закрыть</button></div>', () => {
          document.getElementById('fundingFormClose').onclick = closeModal;
          submitOnce(document.getElementById('fundingCancelSave'), 'Сохраняю…', async () => {
            try {
              const reason = document.getElementById('fundingCancelReason').value.trim();
              if (reason.length < 5) throw new Error('Опишите причину отмены — не короче пяти символов');
              await api('/funding/entries/' + entry.id + '/cancel', { method: 'POST', body: { reason, request_id: key } });
              closeModal(); toast('Отмена сохранена в истории'); refresh();
            } catch (error) { formError(error.message); }
          });
        });
    }

    async function history(planId, offset) {
      try {
        const changes = await api('/funding/plans/' + planId + '/history?skip=' + offset);
        const row = byId.get(String(planId));
        modal('История плана' + (row ? ': ' + row.name : ''),
          '<div class="funding-history">' + changes.items.map(item => '<article><div class="funding-entry__top"><strong>Версия ' + item.revision + '</strong><span class="row__sub">' + dateRu(item.at) + '</span></div>' +
            '<p>' + (item.before ? money(item.before.amount) : 'План не задан') + ' → ' + money(item.after.amount) + '</p>' +
            (item.after.note ? '<p class="funding-note">' + esc(item.after.note) + '</p>' : '') + '<span class="row__sub">' + esc(item.actor) + '</span></article>').join('') + '</div>' +
          '<div class="btn-row"><button class="btn btn--ghost" id="fundingHistoryPrev"' + (offset ? '' : ' disabled') + '>Предыдущие</button><button class="btn btn--ghost" id="fundingHistoryNext"' + (changes.has_more ? '' : ' disabled') + '>Следующие</button></div>', () => {
            document.getElementById('fundingHistoryPrev').onclick = () => history(planId, Math.max(0, offset - 50));
            document.getElementById('fundingHistoryNext').onclick = () => history(planId, offset + 50);
          });
      } catch (error) { fail(error); }
    }
  }

  window.CabinetFunding = { render, summary, parseRubles };
})();
