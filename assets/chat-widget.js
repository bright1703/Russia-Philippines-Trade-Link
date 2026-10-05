/* Чат rusphiltrade.com. Подключение: <script src="/assets/chat-widget.js?v=4" data-lang="en" defer></script>
   v4: память 30 дней (localStorage, общая для всех вкладок и страниц сайта), контекст страницы
   и последние реплики уходят на сервер вместе с сообщением. API прежний: chat.rusphiltrade.com/api.php */
(function () {
  'use strict';
  if (window.__rpcLoaded) return;
  window.__rpcLoaded = true;

  var API = 'https://chat.rusphiltrade.com/api.php';
  var script = document.currentScript;
  var lang = (script && script.getAttribute('data-lang')) || (document.documentElement.lang || 'en').slice(0, 2);
  lang = lang === 'ru' ? 'ru' : 'en';

  var T = {
    en: {
      launcher: 'Ask us',
      title: 'Russia–Philippines Trade Link',
      subtitle: 'Assistant. Requests go to our trade agent.',
      greeting: 'Hi! We help Philippine companies find suppliers from Russia and check Russian companies before a deal. What can we help you with?',
      buttons: ["I'm looking for a product", 'Check a Russian company', 'How does it work?', 'Other question'],
      placeholder: 'Type your message',
      send: 'Send',
      close: 'Close chat',
      privacy: 'We use your messages only to reply to your request and keep them for 30 days.',
      error: 'Connection problem. Please try again or write to rus.import.ph@gmail.com.',
      ended: 'Conversation closed.'
    },
    ru: {
      launcher: 'Задать вопрос',
      title: 'Russia–Philippines Trade Link',
      subtitle: 'Помощник. Заявки передаём торговому агенту.',
      greeting: 'Здравствуйте! Мы помогаем российским производителям найти покупателей на Филиппинах. Расскажите о вашем товаре, и мы оценим, есть ли у него перспективы на этом рынке.',
      buttons: ['Хочу продавать на Филиппины', 'Как вы работаете', 'Сколько стоит', 'Новости рынка'],
      placeholder: 'Напишите сообщение',
      send: 'Отправить',
      close: 'Закрыть чат',
      privacy: 'Сообщения используем только для ответа на ваш запрос и храним 30 дней.',
      error: 'Проблема со связью. Попробуйте ещё раз или напишите на rus.import.ph@gmail.com.',
      ended: 'Разговор завершён.'
    }
  }[lang];

  // Разговор хранится 30 дней (столько же, сколько сервер хранит сообщения),
  // поэтому посетитель, вернувшийся завтра или открывший другую страницу, продолжает тот же диалог.
  var KEY = 'rpc-state-' + lang, TTL = 30 * 24 * 3600 * 1000, MAX_LOG = 60;
  var store = null;
  try { localStorage.setItem('rpc-test', '1'); localStorage.removeItem('rpc-test'); store = localStorage; }
  catch (e) { try { store = sessionStorage; } catch (e2) {} }
  function fresh() { return { sid: '', log: [], closed: false, open: false, ts: Date.now() }; }
  function load() {
    var s = null;
    try { s = JSON.parse((store && store.getItem(KEY)) || 'null'); } catch (e) {}
    if (!s || !s.log) {
      // Перенос разговора из старой версии виджета (sessionStorage).
      try { s = JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch (e) {}
    }
    if (!s || !s.log || (s.ts && Date.now() - s.ts > TTL)) return fresh();
    // Завершённый разговор не блокирует новый визит: начинаем заново.
    if (s.closed && s.ts && Date.now() - s.ts > 12 * 3600 * 1000) return fresh();
    return s;
  }
  var state = load();
  function save() {
    state.ts = Date.now();
    if (state.log.length > MAX_LOG) state.log = state.log.slice(-MAX_LOG);
    try { store && store.setItem(KEY, JSON.stringify(state)); } catch (e) {}
  }
  function hex(n) { var a = '', c = '0123456789abcdef'; for (var i = 0; i < n; i++) a += c[Math.floor(Math.random() * 16)]; return a; }
  if (!/^[a-f0-9]{24}$/.test(state.sid || '')) state.sid = hex(24);
  var touch = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;

  var css = [
    '.rpc-launch{position:fixed;right:22px;bottom:82px;z-index:60;display:inline-flex;align-items:center;gap:8px;min-height:48px;padding:0 18px;border:0;border-radius:24px;background:#0d5042;color:#fff;font:600 15px/1 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;box-shadow:0 8px 22px rgba(13,80,66,.28);cursor:pointer}',
    '.rpc-launch:hover{background:#0a493c}',
    '.rpc-launch svg{width:20px;height:20px}',
    '.rpc-panel{position:fixed;right:22px;bottom:82px;z-index:61;width:370px;max-width:calc(100vw - 32px);height:560px;max-height:calc(100vh - 110px);display:flex;flex-direction:column;background:#fbfaf6;border:1px solid #cbd9d2;box-shadow:0 18px 50px rgba(18,63,53,.22);font:15px/1.5 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;color:#123f35}',
    '.rpc-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:16px 18px;background:#0d5042;color:#fff}',
    '.rpc-head b{display:block;font:400 18px/1.2 Georgia,"Times New Roman",serif}',
    '.rpc-head small{display:block;margin-top:4px;color:#cfe2da;font-size:13px}',
    '.rpc-x{flex:none;width:36px;height:36px;border:0;background:transparent;color:#fff;font-size:26px;line-height:1;cursor:pointer}',
    '.rpc-log{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px}',
    '.rpc-msg{max-width:86%;padding:10px 13px;white-space:pre-wrap;word-wrap:break-word}',
    '.rpc-bot{align-self:flex-start;background:#fff;border:1px solid #dde7e1}',
    '.rpc-me{align-self:flex-end;background:#e5f0eb}',
    '.rpc-msg a{color:#0d5042;text-decoration:underline;word-break:break-all}',
    '.rpc-btns{display:flex;flex-wrap:wrap;gap:8px}',
    '.rpc-btns button{padding:8px 12px;border:1px solid #0d5042;background:#fff;color:#0d5042;font:600 14px/1.2 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;cursor:pointer;border-radius:16px}',
    '.rpc-btns button:hover{background:#0d5042;color:#fff}',
    '.rpc-typing{align-self:flex-start;color:#7e9289;font-size:14px}',
    '.rpc-form{display:flex;gap:8px;padding:12px;border-top:1px solid #dde7e1;background:#fff}',
    '.rpc-form textarea{flex:1;min-height:44px;max-height:120px;padding:10px 12px;border:1px solid #b9ccc3;resize:none;font:16px/1.4 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;color:#123f35;background:#fff}',
    '.rpc-form textarea:focus{outline:2px solid #edc266;outline-offset:1px}',
    '.rpc-form button{flex:none;padding:0 16px;border:0;background:#0d5042;color:#fff;font:600 15px/1 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;cursor:pointer}',
    '.rpc-form button:disabled{opacity:.5;cursor:default}',
    '.rpc-note{padding:0 12px 10px;background:#fff;color:#7e9289;font-size:12px;line-height:1.4}',
    '.rpc-hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}',
    '.rpc-panel[hidden],.rpc-launch[hidden]{display:none!important}',
    '@media (max-width:640px){.rpc-launch{right:16px;bottom:72px}.rpc-panel{right:0;bottom:0;width:100vw;max-width:100vw;height:100%;max-height:100%;border:0}}'
  ].join('');
  var style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // Ссылки в ответах бота превращаем в кликабельные, остальной текст как есть.
  function linkify(node, text) {
    var re = /(https?:\/\/[^\s<>"')]+[^\s<>"').,!?])|([\w.+-]+@[\w-]+\.[\w.]+)/g, last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) node.appendChild(document.createTextNode(text.slice(last, m.index)));
      var a = document.createElement('a');
      if (m[1]) { a.href = m[1]; a.target = '_blank'; a.rel = 'noopener'; a.textContent = m[1].replace(/^https?:\/\//, ''); }
      else { a.href = 'mailto:' + m[2]; a.textContent = m[2]; }
      node.appendChild(a);
      last = re.lastIndex;
    }
    if (last < text.length) node.appendChild(document.createTextNode(text.slice(last)));
  }

  var icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 17a10 10 0 1 1 4 4l-3.4 1a1 1 0 0 1-1.2-1.2l1-3.4A10 10 0 0 1 3 17Z"/></svg>';
  var launch = el('button', 'rpc-launch');
  launch.type = 'button';
  launch.setAttribute('aria-label', T.launcher);
  launch.innerHTML = icon;
  launch.appendChild(document.createTextNode(T.launcher));

  var panel = el('div', 'rpc-panel');
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', T.title);
  panel.hidden = true;
  var head = el('div', 'rpc-head');
  var titles = el('div');
  titles.appendChild(el('b', null, T.title));
  titles.appendChild(el('small', null, T.subtitle));
  var x = el('button', 'rpc-x', '×');
  x.type = 'button';
  x.setAttribute('aria-label', T.close);
  head.appendChild(titles);
  head.appendChild(x);
  var log = el('div', 'rpc-log');
  log.setAttribute('aria-live', 'polite');
  var form = el('form', 'rpc-form');
  var input = el('textarea');
  input.rows = 1;
  input.maxLength = 800;
  input.placeholder = T.placeholder;
  input.setAttribute('aria-label', T.placeholder);
  var hp = el('input', 'rpc-hp');
  hp.name = 'website';
  hp.tabIndex = -1;
  hp.autocomplete = 'off';
  hp.setAttribute('aria-hidden', 'true');
  var send = el('button', null, T.send);
  send.type = 'submit';
  form.appendChild(input);
  form.appendChild(hp);
  form.appendChild(send);
  panel.appendChild(head);
  panel.appendChild(log);
  panel.appendChild(form);
  panel.appendChild(el('div', 'rpc-note', T.privacy));
  document.body.appendChild(launch);
  document.body.appendChild(panel);

  function addMsg(who, text) {
    var m = el('div', 'rpc-msg ' + (who === 'me' ? 'rpc-me' : 'rpc-bot'));
    if (who === 'me') m.textContent = text; else linkify(m, text);
    log.appendChild(m);
    log.scrollTop = log.scrollHeight;
  }
  function clearButtons() {
    var old = log.querySelectorAll('.rpc-btns');
    for (var i = 0; i < old.length; i++) old[i].remove();
  }
  function addButtons(list) {
    clearButtons();
    if (!list || !list.length || state.closed) return;
    var wrap = el('div', 'rpc-btns');
    list.forEach(function (label) {
      var b = el('button', null, label);
      b.type = 'button';
      b.addEventListener('click', function () { ask(label); });
      wrap.appendChild(b);
    });
    log.appendChild(wrap);
    log.scrollTop = log.scrollHeight;
  }
  function render() {
    log.innerHTML = '';
    addMsg('bot', T.greeting);
    state.log.forEach(function (m) { addMsg(m.who, m.text); });
    if (state.closed) { addMsg('bot', T.ended); lock(); }
    else addButtons(state.log.length ? state.lastButtons : T.buttons);
  }
  function lock() { input.disabled = true; send.disabled = true; }

  var busy = false;
  function ask(text) {
    text = (text || '').trim();
    if (!text || busy || state.closed) return;
    busy = true;
    send.disabled = true;
    clearButtons();
    addMsg('me', text);
    state.log.push({ who: 'me', text: text });
    save();
    var typing = el('div', 'rpc-typing', '…');
    log.appendChild(typing);
    log.scrollTop = log.scrollHeight;
    var mid = hex(16);
    var h1 = document.querySelector('h1');
    var payload = JSON.stringify({ sid: state.sid, mid: mid, lang: lang, page: location.pathname, message: text, website: hp.value,
      // Контекст для сервера: на какой странице посетитель и что уже обсуждали (последние 12 реплик без текущей).
      context: { title: document.title, h1: h1 ? h1.textContent.replace(/\s+/g, ' ').trim() : '', knowledge: 'https://rusphiltrade.com/data/chat-knowledge.json' },
      history: state.log.slice(-13, -1).map(function (m) { return { role: m.who === 'me' ? 'user' : 'assistant', text: m.text }; }) });
    var started = Date.now();
    function attempt(n) {
      // Без ответа за 20 секунд обрываем запрос и пробуем ещё раз: иначе окно ждёт вечно.
      var ctrl = window.AbortController ? new AbortController() : null;
      var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, 20000) : null;
      return fetch(API, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: payload, signal: ctrl ? ctrl.signal : undefined })
        .then(function (r) {
          if (timer) clearTimeout(timer);
          return r.text().then(function (body) {
            try { return JSON.parse(body); } catch (e) {
              var err = new Error('parse');
              err.detail = 'status ' + r.status + ': ' + body.slice(0, 150);
              throw err;
            }
          });
        })
        .catch(function (e) {
          if (timer) clearTimeout(timer);
          if (n < 2) return new Promise(function (res) { setTimeout(res, 1500); }).then(function () { return attempt(n + 1); });
          throw e;
        });
    }
    attempt(1).then(function (data) {
      typing.remove();
      if (data.sid) state.sid = data.sid;
      var reply = data.reply || T.error;
      addMsg('bot', reply);
      state.log.push({ who: 'bot', text: reply });
      state.lastButtons = data.buttons || [];
      state.closed = !!data.closed;
      if (state.closed) lock(); else addButtons(state.lastButtons);
      save();
    }).catch(function (e) {
      typing.remove();
      addMsg('bot', T.error);
      // Сообщаем серверу, что пошло не так, чтобы найти причину.
      try {
        var info = JSON.stringify({ sid: state.sid, mid: mid, lang: lang, page: location.pathname,
          error: (e && (e.name === 'AbortError' ? 'timeout' : e.message)) || String(e), detail: (e && e.detail) || '',
          online: navigator.onLine, seconds: Math.round((Date.now() - started) / 1000),
          net: (navigator.connection && navigator.connection.effectiveType) || '' });
        if (navigator.sendBeacon) navigator.sendBeacon(API + '?diag=1', info);
      } catch (x) {}
    }).then(function () {
      busy = false;
      if (!state.closed) send.disabled = false;
    });
  }

  function open() { panel.hidden = false; launch.hidden = true; state.open = true; save(); if (!input.disabled && !touch) input.focus(); log.scrollTop = log.scrollHeight; }
  function close() { panel.hidden = true; launch.hidden = false; state.open = false; save(); launch.focus(); }
  launch.addEventListener('click', open);
  x.addEventListener('click', close);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !panel.hidden) close(); });
  form.addEventListener('submit', function (e) { e.preventDefault(); var t = input.value; input.value = ''; ask(t); });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event('submit')); }
  });

  // Если разговор продолжили в другой вкладке, показываем актуальную версию.
  window.addEventListener('storage', function (e) {
    if (e.key !== KEY || busy) return;
    var s = load();
    state.sid = s.sid; state.log = s.log; state.closed = s.closed; state.lastButtons = s.lastButtons;
    render();
  });

  render();
})();
