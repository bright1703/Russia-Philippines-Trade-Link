/* Мобильное меню rusphiltrade.com: панель справа на экранах до 1100px.
   Пункты берутся из меню страницы, поэтому на каждой странице свои.
   Снаружи остаются «Для российских поставщиков» (.nav-ru) и «Новости» (.nav-news). */
(function () {
  'use strict';
  var nav = document.querySelector('.site-header .nav-links');
  if (!nav || document.querySelector('.menu-toggle')) return;
  var ru = (document.documentElement.lang || '').slice(0, 2) === 'ru';
  var T = ru ? { open: 'Открыть меню', close: 'Закрыть меню', title: 'Меню' }
             : { open: 'Open menu', close: 'Close menu', title: 'Menu' };

  var css = [
    '.menu-toggle,.menu-drawer,.menu-overlay{display:none}',
    '@media (max-width:1100px){',
    '.js-menu .site-header .nav-links>a:not(.nav-ru):not(.nav-news){display:none!important}',
    '.js-menu .menu-toggle{display:inline-flex;align-items:center;justify-content:center;flex:none;width:44px;height:44px;padding:0;border:1px solid #abc8bb;background:transparent;color:var(--ink,#123f35);cursor:pointer}',
    '.js-menu .menu-toggle svg{width:22px;height:22px}',
    '.menu-overlay.is-open{display:block;position:fixed;inset:0;z-index:80;background:rgba(18,63,53,.35)}',
    '.menu-drawer{position:fixed;top:0;right:0;bottom:0;z-index:81;width:min(360px,86vw);flex-direction:column;background:var(--paper,#fbfaf6);box-shadow:-12px 0 36px rgba(18,63,53,.18);transform:translateX(100%);transition:transform .22s ease}',
    '.menu-drawer.is-open{display:flex;transform:none}',
    '.menu-head{display:flex;align-items:center;justify-content:space-between;padding:14px 16px 14px 22px;border-bottom:1px solid var(--line,#cbd9d2)}',
    '.menu-head b{font-family:var(--serif,Georgia,serif);font-size:20px;font-weight:400;color:var(--ink,#123f35)}',
    '.menu-close{width:44px;height:44px;border:0;background:transparent;color:var(--ink,#123f35);font-size:28px;line-height:1;cursor:pointer}',
    '.menu-list{display:flex;flex-direction:column;padding:8px 0;overflow-y:auto}',
    '.menu-list a{display:flex;align-items:center;min-height:52px;padding:0 22px;border-bottom:1px solid #e3ebe6;color:var(--ink,#123f35);font-size:17px}',
    '.menu-list a:hover,.menu-list a:focus-visible{background:var(--mint,#e5f0eb)}',
    '.menu-cta{margin:auto 22px 24px;display:flex;align-items:center;justify-content:center;gap:9px;min-height:50px;background:var(--green,#0d5042);color:#fff!important;font-size:16px;font-weight:600}',
    '}',
    '@media (max-width:1100px) and (min-width:641px){.js-menu .nav-ru .nav-ru-full{display:inline}.js-menu .nav-ru .nav-ru-short{display:none}}',
    '@media (prefers-reduced-motion:reduce){.menu-drawer{transition:none}}'
  ].join('');
  var style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);
  document.documentElement.classList.add('js-menu');

  var ns = 'http://www.w3.org/2000/svg';
  function icon(d) {
    var svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    var p = document.createElementNS(ns, 'path');
    p.setAttribute('d', d);
    p.setAttribute('fill', 'none');
    p.setAttribute('stroke', 'currentColor');
    p.setAttribute('stroke-width', '1.8');
    p.setAttribute('stroke-linecap', 'round');
    svg.appendChild(p);
    return svg;
  }

  var toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'menu-toggle';
  toggle.setAttribute('aria-label', T.open);
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'site-menu');
  toggle.appendChild(icon('M4 7h16M4 12h16M4 17h16'));
  nav.appendChild(toggle);

  var overlay = document.createElement('div');
  overlay.className = 'menu-overlay';
  var drawer = document.createElement('div');
  drawer.className = 'menu-drawer';
  drawer.id = 'site-menu';
  drawer.setAttribute('role', 'dialog');
  drawer.setAttribute('aria-modal', 'true');
  drawer.setAttribute('aria-label', T.title);
  var head = document.createElement('div');
  head.className = 'menu-head';
  var title = document.createElement('b');
  title.textContent = T.title;
  var close = document.createElement('button');
  close.type = 'button';
  close.className = 'menu-close';
  close.setAttribute('aria-label', T.close);
  close.textContent = '×';
  head.appendChild(title);
  head.appendChild(close);
  var list = document.createElement('nav');
  list.className = 'menu-list';
  list.setAttribute('aria-label', T.title);
  var cta = null;
  Array.prototype.forEach.call(nav.querySelectorAll(':scope > a'), function (a) {
    if (a.classList.contains('nav-ru') || a.classList.contains('nav-news')) return;
    var item = document.createElement('a');
    item.href = a.getAttribute('href');
    item.textContent = a.textContent.trim();
    if (a.classList.contains('nav-cta')) {
      item.className = 'menu-cta';
      item.textContent = item.textContent + ' →';
      cta = item;
    } else {
      list.appendChild(item);
    }
  });
  drawer.appendChild(head);
  drawer.appendChild(list);
  if (cta) drawer.appendChild(cta);
  document.body.appendChild(overlay);
  document.body.appendChild(drawer);

  var isOpen = false;
  var pushed = false;
  function focusables() {
    return drawer.querySelectorAll('a[href],button:not([disabled])');
  }
  function open() {
    if (isOpen) return;
    isOpen = true;
    overlay.classList.add('is-open');
    drawer.classList.add('is-open');
    toggle.setAttribute('aria-expanded', 'true');
    document.documentElement.style.overflow = 'hidden';
    try { history.pushState({ rptMenu: 1 }, ''); pushed = true; } catch (e) { pushed = false; }
    var first = list.querySelector('a');
    (first || close).focus();
  }
  function finish() {
    isOpen = false;
    overlay.classList.remove('is-open');
    drawer.classList.remove('is-open');
    toggle.setAttribute('aria-expanded', 'false');
    document.documentElement.style.overflow = '';
  }
  // Закрытие кнопкой «назад» на телефоне: убираем запись, которую добавили при открытии.
  function closeMenu(returnFocus) {
    if (!isOpen) return;
    finish();
    if (pushed && history.state && history.state.rptMenu) { pushed = false; history.back(); }
    if (returnFocus) toggle.focus();
  }
  toggle.addEventListener('click', open);
  close.addEventListener('click', function () { closeMenu(true); });
  overlay.addEventListener('click', function () { closeMenu(true); });
  window.addEventListener('popstate', function () { if (isOpen) { pushed = false; finish(); } });
  drawer.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('a') : null;
    if (!a) return;
    // Переход по ссылке: запись истории не откатываем, иначе переход отменится.
    if (pushed) { try { history.replaceState(null, ''); } catch (x) {} pushed = false; }
    finish();
  });
  document.addEventListener('keydown', function (e) {
    if (!isOpen) return;
    if (e.key === 'Escape') { closeMenu(true); return; }
    if (e.key === 'Tab') {
      var items = focusables();
      if (!items.length) return;
      var firstEl = items[0], lastEl = items[items.length - 1];
      if (e.shiftKey && document.activeElement === firstEl) { e.preventDefault(); lastEl.focus(); }
      else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); firstEl.focus(); }
    }
  });
  // При повороте экрана или расширении окна панель закрывается сама.
  window.matchMedia('(min-width:1101px)').addEventListener('change', function (m) { if (m.matches) closeMenu(false); });
})();
