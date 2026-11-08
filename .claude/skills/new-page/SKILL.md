---
name: new-page
description: Новая страница сайта — гайд для импортёров или направление поставок — по шаблону существующих страниц, со ссылками в меню, на главной и в sitemap. Use when the owner says "новый гайд", "новая страница", "добавь направление", "add a guide", "new product page".
---

# Новая страница

## 1. Уточни
- Тип: **гайд** (`guides/<slug>/`) или **направление поставок** (`<slug>/` в корне).
- Тема, целевой поисковый запрос филиппинского покупателя, источники фактов
  (материалы поставщика в Google Drive, официальные сайты ведомств).
- Нужна ли русская версия в `for-russian-suppliers/`.

## 2. Сделай
1. Возьми за образец ближайшую похожую страницу (последний гайд:
   `guides/shipping-from-vladivostok-to-the-philippines/index.html`).
   Сохрани шапку, меню, подвал, стили, скрипты, формы.
2. Обнови `<title>`, `meta description`, canonical, Open Graph, `hreflang`,
   JSON-LD (для гайдов — Article/FAQPage; Product/Offer только при реальном
   предложении).
3. Текст — только подтверждённые факты; условия поставки «по запросу».
4. Ссылки на страницу:
   - гайд: карточка в `guides/index.html` и ссылка в `guides-strip` на
     `index.html`;
   - направление: запись в `data/directions.json`, карточка на главной.
5. `sitemap.xml`: новая `<url>` с сегодняшним `lastmod`.
6. Для направлений: `python3 tools/build_chat_knowledge.py`.
7. `/site-check`, затем покажи владельцу локально
   (`python3 -m http.server 8000`) или скриншотом.
8. Коммит после «да»: `Publish guide to <тема>` или `Add <направление> page`.
