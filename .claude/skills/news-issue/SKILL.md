---
name: news-issue
description: Новый выпуск «Philippine trade news» (EN) и «Новости Филиппин» (RU) — отбор новостей об импорте на Филиппины, вывод «What this means / Что это значит», RSS и sitemap. Use when the owner says "выпуск новостей", "новости за неделю", "news issue", or on Monday/Thursday.
---

# Выпуск новостей

Выпуски выходят дважды в неделю (пн и чт). Две страницы обновляются вместе:
`philippine-trade-news/` (EN) и `for-russian-suppliers/novosti-filippin/` (RU).

## Шаги

1. **Номер и дата.** Найди последний `id="issue-N"` в
   `philippine-trade-news/index.html`; новый выпуск — `issue-(N+1)`.
2. **Сбор.** Новости за период с прошлого выпуска: импорт, пошлины, правила
   (DA, BOC, BAI, BPI, FDA, DTI), спрос и цены, логистика, отношения с Россией.
   Только материалы, которых не было в прошлых выпусках. Одинаковые события
   из разных изданий — одна карточка (логика в
   `trade-agent/trade_agent/phnews/dedup.py`).
3. **Карточки** — по 4–8 на выпуск, сгруппированы темами
   (`news-topic`: «Imports, tariffs and rules», «Market, demand and prices»,
   «Logistics and payments»; RU: «Импорт, пошлины и правила» и т. д.).
   Своими словами 1–2 предложения, не копируй статью; дата и ссылка на
   источник с `rel="nofollow noopener"`.
4. **Вывод.** Блок `note-box`: «What this means» / «Что это значит» — что эти
   новости значат для российских поставщиков, какие действия.
5. **Разметка.** Новая `<section class="news-issue" id="issue-N">` вставляется
   первой, по образцу предыдущей: `h2`, `note-box`, `news-item`, `news-share`
   (LinkedIn и Telegram со ссылкой на `#issue-N`), `news-cta`.
6. **RSS.** В обоих `feed.xml` добавь `<item>` первым и обнови `lastBuildDate`.
7. **Sitemap.** `<lastmod>` обеих новостных страниц = сегодня.
8. **Проверка.** `/site-check`, затем покажи владельцу заголовки и вывод.
9. **Публикация** только после «да»: коммит `News page: issue N`, пуш.
10. **Анонс.** Предложи текст поста для t.me/Russia_Philippines_trade (RU)
    и LinkedIn (EN), по желанию — карточку в Canva.
