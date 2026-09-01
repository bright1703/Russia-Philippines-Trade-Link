# DEPLOY — развёртывание на Ubuntu

Инструкция от чистого сервера до работающей системы.
Проверено для Ubuntu 22.04 / 24.04.

## 1. Пакеты

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git sqlite3
```

## 2. Пользователь и каталог

```bash
sudo useradd -r -m -d /opt/trade-agent -s /usr/sbin/nologin tradeagent
sudo mkdir -p /opt/trade-agent
sudo chown tradeagent:tradeagent /opt/trade-agent
```

## 3. Код

```bash
sudo -u tradeagent git clone <URL репозитория> /opt/trade-agent/repo
sudo -u tradeagent cp -r /opt/trade-agent/repo/trade-agent/. /opt/trade-agent/
```

Если репозиторий разворачивается целиком, достаточно, чтобы рабочим каталогом
был каталог `trade-agent` — именно из него запускаются команды `python -m`.

Для прямого режима Telethon нужны API ID, API hash и уже авторизованная
сессия. Каталог `telegram/` нужен только старому экспортному режиму.

## 4. Виртуальное окружение

```bash
cd /opt/trade-agent
sudo -u tradeagent python3 -m venv .venv
sudo -u tradeagent .venv/bin/pip install --upgrade pip
sudo -u tradeagent .venv/bin/pip install -r requirements.txt -c constraints.txt
```

`constraints.txt` фиксирует версии, на которых реально проходят тесты.
`pytest` в production не ставится — он в `requirements-dev.txt`.

## 5. Секреты

```bash
sudo -u tradeagent cp .env.example .env
sudo -u tradeagent nano .env
sudo chmod 600 /opt/trade-agent/.env
```

Заполнить как минимум:

```
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=123456:AA...
TELEGRAM_ALLOWED_USER_ID=123456789
# Необязательно: дополнительное ограничение по чатам.
# Пусто — разрешён только личный чат владельца.
# TELEGRAM_ALLOWED_CHAT_ID=
```

Бот отвечает только в личном чате разрешённому пользователю. Сообщения из
групп, супергрупп и каналов игнорируются полностью.

Offset обновлений хранится в `data/bot_offset.json` и пишется атомарно.
При первом запуске накопившиеся обновления пропускаются, а не выполняются,
поэтому после перезапуска бот не разбирает старую очередь заново.

`TELEGRAM_ALLOWED_USER_ID` — числовой id вашего аккаунта (его показывает
любой бот вида @userinfobot). Бот отвечает только этим id.

## 6. Проверка без сети и без ключей

```bash
cd /opt/trade-agent
sudo -u tradeagent env TRADE_AGENT_FIXTURES=tests/fixtures .venv/bin/python -m trade_agent.fetch --days 3650
sudo -u tradeagent .venv/bin/python -m trade_agent.process --mock-llm
sudo -u tradeagent .venv/bin/python -m trade_agent.digest --days 3650
sudo -u tradeagent .venv/bin/pip install -r requirements-dev.txt -c constraints.txt
sudo -u tradeagent .venv/bin/python -m pytest -q
```

Проверка кодов завершения:

```bash
sudo -u tradeagent .venv/bin/python -m trade_agent.fetch --days 7; echo "код: $?"
# 0 — успех, 1 — частичный сбой, 2 — критический сбой
```

Затем очистить тестовые данные:

```bash
sudo -u tradeagent rm -f data/trade_agent.db*
```

## 7. Первый настоящий запуск

```bash
cd /opt/trade-agent
sudo -u tradeagent .venv/bin/python -m trade_agent.fetch --days 7 -v
sudo -u tradeagent .venv/bin/python -m trade_agent.process --limit 50 -v
sudo -u tradeagent .venv/bin/python -m trade_agent.digest
cat digest/latest.md
sudo -u tradeagent .venv/bin/python -m trade_agent.notify

# единый запуск вместо трех отдельных команд
sudo -u tradeagent .venv/bin/python -m trade_agent.run_pipeline
```

## 8. Профили компаний

```bash
sudo -u tradeagent nano brain/companies/td-vik.md      # заполнить вручную
# либо загрузить каталог целиком:
sudo -u tradeagent .venv/bin/python -m trade_agent.companies.import_companies catalog.csv --write-profiles
```

## 9. Расписание (systemd, рекомендуется)

```bash
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trade-agent-fetch.timer
sudo systemctl enable --now trade-agent-process.timer
sudo systemctl enable --now trade-agent-digest.timer
sudo systemctl enable --now trade-agent-bot.service
```

Проверка:

```bash
systemctl list-timers | grep trade-agent
journalctl -u trade-agent-fetch.service -n 50 --no-pager
systemctl status trade-agent-bot.service
```

Время в unit-файлах указано в UTC (`OnCalendar=*-*-* 05:30:00`).
Изменить — отредактировать таймер и выполнить `sudo systemctl daemon-reload`.

## 10. Расписание (cron, альтернатива)

```bash
sudo -u tradeagent crontab -e
# вставить содержимое deploy/crontab.example
```

## 11. Обновление

```bash
cd /opt/trade-agent/repo && sudo -u tradeagent git pull
sudo -u tradeagent cp -r /opt/trade-agent/repo/trade-agent/trade_agent /opt/trade-agent/
sudo -u tradeagent /opt/trade-agent/.venv/bin/pip install -r /opt/trade-agent/requirements.txt
sudo systemctl restart trade-agent-bot.service
```

База, `.env`, `brain/` и `digest/` при обновлении не трогаются.

## 12. Резервная копия

```bash
sudo -u tradeagent sqlite3 data/trade_agent.db ".backup '/opt/trade-agent/backup-$(date +%F).db'"
sudo tar czf /root/trade-agent-brain-$(date +%F).tar.gz -C /opt/trade-agent brain digest
```

Копировать `.env` в общий бэкап не нужно — храните его отдельно.

## 13. Диагностика

| Симптом | Что смотреть |
|---|---|
| нет новых материалов | `journalctl -u trade-agent-fetch`, раздел ошибок в `runs` |
| очередь растёт | не задан `ANTHROPIC_API_KEY` или исчерпан лимит вызовов |
| бот молчит | `TELEGRAM_ALLOWED_USER_ID` не совпадает с вашим id |
| дайджест пустой | пороги слишком высокие, снизьте `DIGEST_MIN_CONFIDENCE` |
| код возврата 1 у process | есть отложенные или неподтверждённые сигналы — это норма при недоступной модели |
| сигналы в статусе failed | рецензия не состоялась: смотреть `signals.last_error` |
| бот повторяет старые команды | удалён или повреждён `data/bot_offset.json` |
| `database is locked` | два этапа запущены одновременно, разведите по времени |

## 13a. Веб-источники

Все веб-источники в `sources.yml` выключены. Включать по одному, каждый
раз проверяя адаптер на живой странице:

```bash
# 1. посмотреть, что реально извлекается, ничего не записывая
python -m trade_agent.fetch --source bai --days 7 --dry-run -v
# 2. если результат осмысленный — включить enabled: true в sources.yml
```

До такой проверки источник считается заготовкой, а не рабочим.

## 14. Безопасность

* `.env` — права 600, в Git не попадает;
* Telethon-сессия и база в Git не попадают (см. `.gitignore`);
* бот принимает команды только от id из белого списка;
* все внешние обращения — только чтение;
* unit-файлы запускают систему без привилегий, с `ProtectSystem=strict`.
