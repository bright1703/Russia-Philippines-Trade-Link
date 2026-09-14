#!/usr/bin/env python3
"""
Снимок состояния сервера перед любыми изменениями.

    python tools/server_snapshot.py --out ~/trade-agent-snapshot-$(date +%F).md

Скрипт ТОЛЬКО читает. Он ничего не останавливает, не включает, не
переключает и не удаляет. Секреты не печатаются: из .env берутся имена
переменных, но не значения.

Фиксируется:
  * текущий commit и активная ветка, незакоммиченные изменения;
  * пользовательские и системные timer/service ЭТОГО проекта;
  * cron этого проекта;
  * конфигурация (публичная часть) и sources.yml;
  * состояние базы: размер, WAL, счётчики таблиц, позиции источников;
  * файлы выпусков и состояние доставки.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# Юниты и задания других проектов на этой машине трогать нельзя.
OTHER_PROJECTS = ("tour-phil", "lazy-reader", "price-collector")


def sh(command: list[str], timeout: int = 20) -> str:
    """Выполняет команду и возвращает вывод. Ошибка — тоже результат."""
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return f"(команда {command[0]} недоступна)"
    except subprocess.TimeoutExpired:
        return f"(таймаут {timeout}s)"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    return out or err or "(пусто)"


def block(title: str, body: str) -> str:
    return f"## {title}\n\n```\n{body}\n```\n"


def find_git_root(start: Path) -> Optional[Path]:
    """Git-корень может быть выше каталога установки."""
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def git_state(repo: Path) -> str:
    root = find_git_root(repo)
    if root is None:
        return (f"(в {repo} и выше git-репозиторий не найден — "
                "вероятно, код развёрнут копированием, а не клоном)")
    repo = root
    parts = [
        "commit:  " + sh(["git", "-C", str(repo), "rev-parse", "HEAD"]),
        "ветка:   " + sh(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"]),
        "описание:" + sh(["git", "-C", str(repo), "log", "-1", "--format=%h %ad %s",
                          "--date=iso"]),
        "",
        "незакоммиченные изменения:",
        sh(["git", "-C", str(repo), "status", "--short"]),
        "",
        "последние коммиты:",
        sh(["git", "-C", str(repo), "log", "--oneline", "-5"]),
    ]
    return "\n".join(parts)


def systemd_state() -> str:
    """Юниты проекта. Чужие проекты только перечисляются, чтобы их не трогать."""
    parts = [
        "--- пользовательские таймеры проекта ---",
        sh(["systemctl", "--user", "list-timers", "--all", "--no-pager"]),
        "",
        "--- системные таймеры ---",
        sh(["systemctl", "list-timers", "--all", "--no-pager"]),
        "",
        "--- юниты со словом trade-agent ---",
        sh(["systemctl", "list-unit-files", "--no-pager", "trade-agent*"]),
        sh(["systemctl", "--user", "list-unit-files", "--no-pager", "trade-agent*"]),
    ]
    text = "\n".join(parts)
    found_others = [name for name in OTHER_PROJECTS if name in text.lower()]
    if found_others:
        text += ("\n\nВНИМАНИЕ: на машине есть юниты других проектов "
                 f"({', '.join(found_others)}). Их трогать нельзя.")
    return text


def cron_state() -> str:
    user_cron = sh(["crontab", "-l"])
    lines = [line for line in user_cron.splitlines()
             if "trade_agent" in line or "trade-agent" in line]
    return ("--- строки crontab, относящиеся к trade-agent ---\n"
            + ("\n".join(lines) if lines else "(нет)")
            + "\n\n--- весь crontab пользователя ---\n" + user_cron)


def env_state(project: Path) -> str:
    """Имена переменных из .env без значений."""
    env_file = project / ".env"
    if not env_file.exists():
        return "(.env не найден)"
    names = []
    for line in env_file.read_text("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        names.append(line.split("=", 1)[0].strip())
    return ("заданные переменные (значения не показываются):\n"
            + "\n".join(f"  {name}" for name in sorted(set(names))))


def db_state(db_path: Path) -> str:
    if not db_path.exists():
        return f"(база {db_path} не найдена)"
    from trade_agent.db import Database

    parts = [f"файл:    {db_path}",
             f"размер:  {db_path.stat().st_size} байт"]
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            parts.append(f"{suffix}:    {side.stat().st_size} байт "
                         "(есть незаписанные данные: копировать только .backup)")

    database = Database(db_path)
    try:
        stats = database.stats()
        parts.append("")
        parts.append("счётчики:")
        for key, value in stats.items():
            if key == "last_error":
                continue
            parts.append(f"  {key}: {value}")
        if stats.get("last_error"):
            parts.append(f"  последняя ошибка: {stats['last_error'][:200]}")

        parts.append("")
        parts.append("позиции источников:")
        states = database.all_source_states()
        if not states:
            parts.append("  (таблица source_state пуста — прежняя версия кода "
                         "позиции не вела)")
        for state in states:
            parts.append(
                f"  {state.source_id}: успех={state.last_success_at or '—'} "
                f"публикация={state.last_published_at or '—'} "
                f"полный_период={state.coverage_complete} "
                f"ошибка={state.last_error[:60] or '—'}")

        parts.append("")
        parts.append("выпуски:")
        try:
            rows = database.conn.execute(
                "SELECT id, status, built_at, content_hash FROM issues "
                "ORDER BY id DESC LIMIT 5").fetchall()
            for row in rows:
                parts.append(f"  №{row['id']} {row['status']} {row['built_at']} "
                             f"{(row['content_hash'] or '')[:12]}")
            if not rows:
                parts.append("  (нет)")
        except Exception as exc:  # noqa: BLE001
            parts.append(f"  (таблицы issues нет: {exc})")

        parts.append("")
        parts.append("доставки:")
        try:
            rows = database.conn.execute(
                "SELECT issue_id, chat_id, status, parts_sent, parts_total "
                "FROM deliveries ORDER BY id DESC LIMIT 5").fetchall()
            for row in rows:
                parts.append(f"  выпуск {row['issue_id']} → чат {row['chat_id']}: "
                             f"{row['status']} {row['parts_sent']}/{row['parts_total']}")
            if not rows:
                parts.append("  (нет)")
        except Exception as exc:  # noqa: BLE001
            parts.append(f"  (таблицы deliveries нет: {exc})")

        parts.append("")
        parts.append("последние запуски:")
        for run in database.recent_runs(8):
            parts.append(f"  {run.stage}: {run.status} {run.started_at} "
                         f"обработано={run.processed} создано={run.created} "
                         f"ошибок={run.errors}")
    finally:
        database.close()
    return "\n".join(parts)


def files_state(project: Path) -> str:
    parts = []
    for relative in ("digest/latest.md", "sources.yml", "data", "logs"):
        path = project / relative
        if not path.exists():
            parts.append(f"{relative}: (нет)")
            continue
        if path.is_dir():
            entries = sorted(p.name for p in path.iterdir())[:12]
            parts.append(f"{relative}/: {len(list(path.iterdir()))} объектов "
                         f"({', '.join(entries)})")
        else:
            parts.append(f"{relative}: {path.stat().st_size} байт, "
                         f"изменён {path.stat().st_mtime:.0f}")
    return "\n".join(parts)


def build(project: Path, repo: Optional[Path] = None) -> str:
    from trade_agent.config import load_settings

    settings = load_settings(project)
    repo = repo or project
    sections = [
        "# Снимок состояния trade-agent перед изменениями",
        "",
        f"Каталог проекта: {project}",
        f"Пользователь: {os.environ.get('USER', 'неизвестен')}",
        f"Хост: {sh(['hostname'])}",
        f"Время сервера: {sh(['date', '-Is'])}",
        f"Часовой пояс: {sh(['sh', '-c', 'timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone'])}",
        "",
        "Снимок сделан только чтением. Ничего не остановлено, не включено "
        "и не удалено.",
        "",
        block("Git", git_state(repo)),
        block("systemd", systemd_state()),
        block("cron", cron_state()),
        block("Секреты (.env)", env_state(project)),
        block("Настройки (публичная часть)",
              json.dumps(settings.public_dict(), ensure_ascii=False, indent=2)),
        block("sources.yml",
              (project / "sources.yml").read_text("utf-8")
              if (project / "sources.yml").exists() else "(нет)"),
        block("База данных", db_state(Path(settings.db_path))),
        block("Файлы", files_state(project)),
    ]
    return "\n".join(sections)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python tools/server_snapshot.py",
        description="Снимок состояния сервера перед изменениями (только чтение).")
    parser.add_argument("--project", type=Path, default=PROJECT_DIR,
                        help="каталог рабочей установки trade-agent")
    parser.add_argument("--repo", type=Path, default=None,
                        help="каталог git-репозитория, если он отдельно")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    report = build(args.project.resolve(), args.repo.resolve() if args.repo else None)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, "utf-8")
        print(f"Снимок записан: {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
