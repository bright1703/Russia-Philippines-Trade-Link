# Tender Radar — Philippines

Радар установлен, но ещё не запускался на реальных источниках.

Первый запуск:

```bash
cd trade-agent/tenders
python fetch_tenders.py --dry-run     # проверка без записи
python fetch_tenders.py               # боевой запуск
```

Пример готового дайджеста (собран на локальных фикстурах, без сети):
`tests/fixtures/expected_digest_example.md`
