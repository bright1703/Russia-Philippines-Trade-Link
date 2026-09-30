/* Формы rusphiltrade.com: FormSubmit (письмо) + копия на сервер (Telegram),
   скрытые поля, проверка, переход на страницу благодарности, цели аналитики. */
(function () {
  'use strict';
  var FORMSUBMIT = 'https://formsubmit.co/ajax/rus.import.ph@gmail.com';
  var SERVER = 'https://chat.rusphiltrade.com/inquiry.php';
  var ru = (document.documentElement.lang || '').slice(0, 2) === 'ru';
  var T = ru ? {
    required: 'Заполните это поле.', email: 'Проверьте адрес почты.', consent: 'Нужно согласие с политикой конфиденциальности.',
    check: 'Проверьте отмеченные поля.', sending: 'Отправляем...', failed: 'Не удалось отправить. Напишите, пожалуйста, на rus.import.ph@gmail.com.',
    thanks: '/for-russian-suppliers/thank-you/'
  } : {
    required: 'Please fill in this field.', email: 'Please enter a valid email address.', consent: 'Please accept the Privacy Policy.',
    check: 'Please check the highlighted fields.', sending: 'Sending...', failed: 'The form could not be sent. Please email rus.import.ph@gmail.com.',
    thanks: '/thank-you/'
  };

  // Метки UTM сохраняем на входе, чтобы они дошли до формы на другой странице.
  try {
    var q = new URLSearchParams(location.search), utm = {};
    ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'].forEach(function (k) { if (q.get(k)) utm[k] = q.get(k); });
    if (Object.keys(utm).length) sessionStorage.setItem('rpt-utm', JSON.stringify(utm));
    if (!sessionStorage.getItem('rpt-ref')) sessionStorage.setItem('rpt-ref', document.referrer || '');
  } catch (e) {}

  function goal(name, params) {
    try { if (window.gtag) window.gtag('event', name, params || {}); } catch (e) {}
    try { if (window.ym) window.ym(112106855, 'reachGoal', name, params || {}); } catch (e) {}
  }

  // Клики по почте, WhatsApp и PDF.
  document.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) return;
    var href = a.getAttribute('href') || '';
    if (/^mailto:/i.test(href)) goal('email_click');
    else if (/wa\.me|whatsapp/i.test(href)) goal('whatsapp_click');
    else if (/\.pdf(\?|#|$)/i.test(href)) goal('download', { file: href });
    // Кнопки, которые заполняют форму: товар и тип обращения.
    var pre = e.target.closest ? e.target.closest('.js-prefill') : null;
    if (pre) {
      var form = document.querySelector('form[data-rpt-form]');
      if (!form) return;
      if (pre.dataset.product && form.elements.product) form.elements.product.value = pre.dataset.product;
      if (pre.dataset.inquiry && form.elements.inquiry_type) form.elements.inquiry_type.value = pre.dataset.inquiry;
    }
  });

  function label(el) {
    return el.getAttribute('data-label') || el.name;
  }

  Array.prototype.forEach.call(document.querySelectorAll('form[data-rpt-form]'), function (form) {
    var status = form.querySelector('.form-status');
    var button = form.querySelector('button[type="submit"]');
    form.setAttribute('novalidate', '');
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      form.querySelectorAll('.field-error').forEach(function (n) { n.remove(); });
      form.querySelectorAll('[aria-invalid]').forEach(function (n) { n.removeAttribute('aria-invalid'); });
      var errors = [];
      Array.prototype.forEach.call(form.elements, function (el) {
        if (!el.name || el.type === 'submit' || el.name === '_honey') return;
        var v = el.type === 'checkbox' ? el.checked : (el.value || '').trim();
        var msg = '';
        if (el.required && !v) msg = el.type === 'checkbox' ? T.consent : T.required;
        else if (el.type === 'email' && v && !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v)) msg = T.email;
        if (msg) {
          errors.push(el);
          el.setAttribute('aria-invalid', 'true');
          var n = document.createElement('span');
          n.className = 'field-error';
          n.textContent = msg;
          (el.type === 'checkbox' ? el.closest('label') : el).insertAdjacentElement('afterend', n);
        }
      });
      if (errors.length) { status.textContent = T.check; errors[0].focus(); return; }
      if (form.elements._honey && form.elements._honey.value) return;

      var type = form.getAttribute('data-rpt-form');
      var raw = { form_type: type }, pretty = {};
      Array.prototype.forEach.call(form.elements, function (el) {
        if (!el.name || el.type === 'submit' || el.name === '_honey') return;
        var v = el.type === 'checkbox' ? (el.checked ? 'yes' : 'no') : (el.tagName === 'SELECT' ? el.options[el.selectedIndex].text : el.value.trim());
        raw[el.name] = el.tagName === 'SELECT' ? el.value : v;
        if (v !== '' && el.name !== 'consent') pretty[label(el)] = v;
      });
      var utm = {};
      try { utm = JSON.parse(sessionStorage.getItem('rpt-utm') || '{}'); } catch (e) {}
      var extra = { page_url: location.href.split('#')[0], language: ru ? 'ru' : 'en',
                    referrer: (function () { try { return sessionStorage.getItem('rpt-ref') || ''; } catch (e) { return document.referrer; } })() };
      Object.keys(utm).forEach(function (k) { extra[k] = utm[k]; });
      Object.keys(extra).forEach(function (k) { raw[k] = extra[k]; if (extra[k]) pretty[k] = extra[k]; });
      var subject = (ru ? 'Заявка с сайта: ' : 'Website inquiry: ') + (raw.product || raw.company || type);
      pretty._subject = subject; pretty._template = 'table'; pretty._captcha = 'false';
      if (raw.email) pretty.email = raw.email;

      button.disabled = true;
      status.textContent = T.sending;
      function post(url, body) {
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body) })
          .then(function (r) { return r.ok; }).catch(function () { return false; });
      }
      Promise.all([post(FORMSUBMIT, pretty), post(SERVER, raw)]).then(function (res) {
        if (!res[0] && !res[1]) { status.textContent = T.failed; button.disabled = false; return; }
        var done = false;
        function go() { if (done) return; done = true; location.href = T.thanks + '?t=' + encodeURIComponent(type); }
        try { if (window.gtag) window.gtag('event', 'form_submit', { form_type: type, event_callback: go }); } catch (e) {}
        try { if (window.ym) window.ym(112106855, 'reachGoal', 'form_submit', { form_type: type }); } catch (e) {}
        setTimeout(go, 700);
      });
    });
  });
})();
