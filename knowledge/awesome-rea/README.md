# База знаний: Awesome-REA

**Awesome Reverse Engineering Anything** ([awesomerea.org](https://awesomerea.org/), [GitHub](https://github.com/reacher-z/Awesome-REA)) — двуязычный (EN / 简体中文) курируемый каталог инструментов, кейсов и научных статей по реверс-инжинирингу в шести областях: сайты, игры, приложения, нативные бинарники, прошивки и железо, сетевые и device-протоколы. Лицензия текста каталога — MIT; у каждой ссылки своя лицензия.

Это справочная база, к B2B-сайту rusphiltrade.com и чату для покупателей она не подключена (`data/chat-knowledge.json` не меняется). Папка закрыта от поисковиков в `robots.txt`.

## Что в папке

| Файл | Что внутри | Откуда |
| --- | --- | --- |
| `README.md` | Этот конспект (пишется вручную) | — |
| `INDEX.md` | Все 1 253 ресурса по категориям + 235 статей по годам, по строке на запись | генерируется |
| `resources.json` | Ресурсы: id, name, url, entry_url, category, kind, relationship, targets, description, requirements, verified_at | `api/v1/resources.json` |
| `papers.json` | Статьи: id, title, year, venue, authors, url, code_url, targets, description | `api/v1/papers.json` |
| `categories.json` | Категории, разделы, типы, цели и связи со счётчиками (как есть) | `api/v1/categories.json` |
| `llms.txt` | Навигационный файл сайта для LLM (как есть) | `/llms.txt` |
| `source.json` | Версия API, дата каталога, дата синхронизации, счётчики, SHA-256 | — |

Срез от **2026-11-07** (catalog `generated_at` 2026-11-07T02:57:12Z, API v1.0.0).

### Обновить

```bash
python3 tools/sync_awesome_rea.py
```

Скрипт читает `llms.txt` и `api/v1/index.json`, скачивает `resources.json`, `papers.json`, `categories.json`, сверяет SHA-256 с индексом (при расхождении останавливается) и перезаписывает файлы выше. Только стандартная библиотека Python. Сайт отклоняет стандартный User-Agent `Python-urllib`, поэтому скрипт отправляет свой.

## Как устроен источник (для машин)

**llms.txt** — `https://awesomerea.org/llms.txt`. Карта сайта для языковых моделей. У каждой страницы списка, категории и записи есть Markdown-версия `index.md` рядом (`<link rel="alternate" type="text/markdown">`); у страниц записей есть `record.json`, у категорий — `index.json`. Полный каталог одним Markdown-файлом: `https://awesomerea.org/llms-full.txt` (~4 МБ), китайская версия — `/zh/llms-full.txt`.

**Статический JSON/CSV API** — `https://awesomerea.org/api/v1/`, без ключа, CORS `*`, ETag (кэшировать и слать `If-None-Match`). Каждый файл содержит `version`, `generated_at` и ссылку на схему.

| Эндпоинт | Записей | Содержание |
| --- | ---: | --- |
| `index.json` | — | Список файлов с размером и SHA-256 |
| `resources.json` / `.csv` | 1 253 | Курируемые ресурсы |
| `papers.json` / `.csv` | 235 | Библиография |
| `categories.json` / `.csv` | 69 | Категории, разделы, типы, цели, связи; плюс категории репозиториев |
| `candidates.json` / `.csv` | 2 000 | Непроверенные автоматические находки и лиды |
| `changes.json` / `.csv` | ~1 566 | Изменения за 30 дней |
| `schema.json` | — | JSON Schema для всех файлов |

`generated_at` — самое новое время в данных, а не время сборки. Файлы меняются с каждым смерженным PR и с ежечасным приёмом находок.

Прочее: `catalog.json` (весь курируемый каталог), `downloads/records.jsonl`, `feed.xml` / `feed.json`, `data-manifest.json` с SHA-256, зеркало на Hugging Face `AgPerry/awesome-rea` (Parquet/JSONL, раз в 2 часа).

Пример запроса:

```bash
curl -s https://awesomerea.org/api/v1/resources.json \
  | jq '.resources[] | select(.targets | index("protocol")) | {id, name, entry_url}'
```

### Поля записи ресурса

`id` (стабильный), `name` / `name_zh`, `url` (первоисточник), `entry_url` / `markdown_url` / `json_url` (страница каталога), `category`, `kind`, `relationship`, `targets`, `description_en` / `description_zh`, `requirements`, `evidence_url` (ссылка на ревью, закреплённая на коммите), `verification` (у всех `source-reviewed`), `verified_at`, `added_at`; у части записей `requirement_facets`, `reconstruction_method`.

`source-reviewed` значит, что описание сверено с первичной документацией. Это не значит, что инструмент запускали, проверяли на безопасность или совместимость. У статей `runtime_tested: false`.

## Состав каталога

**По типу:** tool 607 · case-study 431 · integration 96 · learning-resource 44 · collection 32 · documentation 29 · research 10 · project 4.

**По цели (у записи может быть несколько):** binary 472 · game 377 · app 306 · protocol 242 · firmware 149 · website 90.

**Разделы README:** Website 61 · Game 318 · App 174 · Native Binary 269 · Firmware and Hardware 89 · Protocol 140 · Agents and MCP 87 · Related Collections 32 · References and Learning 80.

**Связь с REA.** «REA» — отдельный upstream-проект [morluto/rea](https://github.com/morluto/rea): CLI и MCP-сервер для расследований поставляемого ПО с сохранением доказательств (Node.js ≥ 22.19). Метки: Official REA (материалы проекта), Community REA (сторонние работы), Independent MCP / agent / reconstruction (без подтверждённой связи), Mixed workflow (REA вместе с другими инструментами), foundation (базовые инструменты), learning.

**Непроверенная часть:** 3 309 автоматически найденных репозиториев (9 категорий: Native 837, General 654, Games 491, Android 482, Firmware 228, Websites 217, Runtime/debugging 152, Managed code 135, Protocols 113), очередь discoveries (~6 000) и лиды из внешних списков (~12 000). Это не ревьюилось, в локальную копию не входит; доступно через `candidates.json` и `browse/*/index.json`.

## Ключевые инструменты по областям

Всё ниже есть в каталоге (полный список — `INDEX.md`).

- **Нативные бинарники:** Ghidra, radare2, Rizin, Cutter, ImHex. Библиотеки: angr, Capstone, Unicorn Engine, LIEF, Kaitai Struct. Сравнение бинарников: BinDiff, Diaphora.
- **Отладка и динамика:** Frida, x64dbg, Qiling.
- **Android:** JADX, Apktool, objection. **.NET:** ILSpy. **JS/Electron:** webcrack. **WebAssembly:** WABT.
- **Сайты:** mitmproxy и 41 инструмент для инспекции, деобфускации и пересборки.
- **Прошивки:** Binwalk, unblob, Ghidra Firmware Utilities.
- **Протоколы:** Wireshark, Scapy.
- **Игры:** decomp.me, 382 кейса независимых декомпиляций, рекомпиляций, портов и реимплементаций движков.
- **Агенты и MCP:** GhidraMCP, IDA Pro MCP, Binary Ninja MCP (официальный), Radare2 MCP, JADX MCP, x64dbg MCP, Wireshark MCP, Burp Suite MCP Server — всего 46 MCP-интеграций и 38 агентных воркфлоу.
- **Статьи:** 235 работ, например LLM4Decompile (EMNLP 2024): языковые модели для декомпиляции ассемблера в C и доработки псевдокода Ghidra.

## Правила проекта (из AGENTS.md / CONTRIBUTING.md)

- Исходные данные: `data/tool-resources.json` (независимые ресурсы), `data/rea-resources.json` (связи с REA), `data/papers.json`; китайские названия — `site/translations.zh-CN.json`.
- Генерируемые блоки README, `docs/papers*.md`, `docs/cases*.md`, `docs/repositories/` руками не правят: `scripts/build_readme.py`, `scripts/build_repository_index.py`, `scripts/validate.py`.
- Поиск по каталогу в клоне репозитория: `python3 scripts/catalog.py <query>` (`--papers`, `--source-index`).
- Предложения — через issue-формы (resource, case-study, paper, broken-resource, translation) или PR от форка; доказательства — первичные источники, ссылки на GitHub закрепляются на коммите.
