# Trade Agent — ежедневный дайджест

Система развёрнута, но ещё не запускалась на реальных источниках.

Первый запуск:

```bash
python -m trade_agent.fetch
python -m trade_agent.process
python -m trade_agent.digest
```

Проверка без сети и без ключей (данные синтетические, это не анализ):

```bash
TRADE_AGENT_FIXTURES=tests/fixtures python -m trade_agent.fetch --days 3650
python -m trade_agent.process --mock-llm
python -m trade_agent.digest --days 3650
```

Коды возврата: 0 — успех, 1 — частичный сбой, 2 — критический сбой.
