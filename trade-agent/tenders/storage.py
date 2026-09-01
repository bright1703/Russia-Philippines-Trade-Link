"""
Хранилище найденных тендеров.

Интерфейс TenderStore намеренно узкий, чтобы SQLite можно было
заменить на любое другое хранилище без правки остальной логики.
По умолчанию используется SQLite (tenders.db, в Git не попадает).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol

from normalize import Notice

CHANGE_NEW = "new"
CHANGE_DEADLINE = "deadline_changed"
CHANGE_STATUS = "status_changed"
CHANGE_DOCUMENT = "document_added"
CHANGE_CLOSED = "closed"
CHANGE_CANCELLED = "cancelled"
CHANGE_SCORE = "score_changed"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


class TenderStore(Protocol):
    def upsert(self, notice: Notice) -> tuple[bool, list[dict[str, str]]]: ...
    def all_notices(self) -> list[Notice]: ...
    def history(self, canonical_id: str) -> list[dict[str, Any]]: ...
    def record_run(self, summary: dict[str, Any]) -> None: ...
    def close(self) -> None: ...


def _diff(old: dict[str, Any], new: Notice) -> list[dict[str, str]]:
    """Сравнивает сохранённую версию с новой и описывает изменения."""
    changes: list[dict[str, str]] = []
    if (old.get("closing_date") or "") != (new.closing_date or ""):
        changes.append({
            "type": CHANGE_DEADLINE, "field": "closing_date",
            "old": old.get("closing_date") or "", "new": new.closing_date or "",
        })
    old_status = old.get("status") or ""
    if old_status != new.status:
        ctype = CHANGE_STATUS
        if new.status == "closed":
            ctype = CHANGE_CLOSED
        elif new.status == "cancelled":
            ctype = CHANGE_CANCELLED
        changes.append({"type": ctype, "field": "status", "old": old_status, "new": new.status})
    old_docs = set(old.get("attachment_urls") or [])
    added = [u for u in new.attachment_urls if u not in old_docs]
    for url in added:
        changes.append({"type": CHANGE_DOCUMENT, "field": "attachment_urls", "old": "", "new": url})
    if int(old.get("match_score") or 0) != int(new.match_score or 0):
        changes.append({
            "type": CHANGE_SCORE, "field": "match_score",
            "old": str(old.get("match_score") or 0), "new": str(new.match_score),
        })
    return changes


class SqliteStore:
    """Основное хранилище. Файл БД не коммитится (см. .gitignore)."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS notices (
        canonical_id  TEXT PRIMARY KEY,
        notice_id     TEXT,
        title         TEXT,
        agency        TEXT,
        source_ids    TEXT,
        closing_date  TEXT,
        status        TEXT,
        match_score   INTEGER,
        first_seen    TEXT,
        last_seen     TEXT,
        payload       TEXT
    );
    CREATE TABLE IF NOT EXISTS notice_history (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_id TEXT NOT NULL,
        changed_at   TEXT NOT NULL,
        change_type  TEXT NOT NULL,
        field        TEXT,
        old_value    TEXT,
        new_value    TEXT
    );
    CREATE TABLE IF NOT EXISTS runs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT,
        summary    TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_history_notice ON notice_history(canonical_id);
    CREATE INDEX IF NOT EXISTS idx_notices_closing ON notices(closing_date);
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(self.SCHEMA)
        self.conn.commit()

    # -- чтение -----------------------------------------------------------
    def get(self, canonical_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT payload FROM notices WHERE canonical_id = ?", (canonical_id,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def all_notices(self) -> list[Notice]:
        rows = self.conn.execute("SELECT payload FROM notices").fetchall()
        return [Notice(**json.loads(r["payload"])) for r in rows]

    def history(self, canonical_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT changed_at, change_type, field, old_value, new_value "
            "FROM notice_history WHERE canonical_id = ? ORDER BY id",
            (canonical_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- запись -----------------------------------------------------------
    def upsert(self, notice: Notice) -> tuple[bool, list[dict[str, str]]]:
        """Возвращает (это_новое_объявление, список_изменений)."""
        existing = self.get(notice.canonical_id)
        now = _now()
        if existing is None:
            notice.first_seen = notice.first_seen or now
            notice.last_seen = now
            self._write(notice)
            self._log(notice.canonical_id, CHANGE_NEW, "", "", notice.title)
            return True, [{"type": CHANGE_NEW, "field": "", "old": "", "new": notice.title}]

        notice.first_seen = existing.get("first_seen") or now
        notice.last_seen = now
        # Не теряем документы, найденные раньше.
        for url in existing.get("attachment_urls") or []:
            if url not in notice.attachment_urls:
                notice.attachment_urls.append(url)
        for url in existing.get("source_links") or []:
            if url and url not in notice.source_links:
                notice.source_links.append(url)
        changes = _diff(existing, notice)
        self._write(notice)
        for ch in changes:
            self._log(notice.canonical_id, ch["type"], ch["field"], ch["old"], ch["new"])
        return False, changes

    def _write(self, notice: Notice) -> None:
        self.conn.execute(
            """INSERT INTO notices
               (canonical_id, notice_id, title, agency, source_ids, closing_date,
                status, match_score, first_seen, last_seen, payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(canonical_id) DO UPDATE SET
                 notice_id=excluded.notice_id, title=excluded.title, agency=excluded.agency,
                 source_ids=excluded.source_ids, closing_date=excluded.closing_date,
                 status=excluded.status, match_score=excluded.match_score,
                 last_seen=excluded.last_seen, payload=excluded.payload""",
            (
                notice.canonical_id, notice.notice_id, notice.title, notice.agency,
                ",".join(notice.source_ids), notice.closing_date, notice.status,
                int(notice.match_score or 0), notice.first_seen, notice.last_seen,
                json.dumps(notice.to_dict(), ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def _log(self, cid: str, ctype: str, field_name: str, old: str, new: str) -> None:
        self.conn.execute(
            "INSERT INTO notice_history (canonical_id, changed_at, change_type, field, old_value, new_value)"
            " VALUES (?,?,?,?,?,?)",
            (cid, _now(), ctype, field_name, old, new),
        )
        self.conn.commit()

    def record_run(self, summary: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO runs (started_at, summary) VALUES (?,?)",
            (_now(), json.dumps(summary, ensure_ascii=False)),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class JsonStore:
    """Запасное хранилище с тем же интерфейсом (для окружений без SQLite)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, Any] = {"notices": {}, "history": {}, "runs": []}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text("utf-8"))
            except (ValueError, OSError):
                pass

    def get(self, canonical_id: str) -> Optional[dict[str, Any]]:
        return self.data["notices"].get(canonical_id)

    def all_notices(self) -> list[Notice]:
        return [Notice(**p) for p in self.data["notices"].values()]

    def history(self, canonical_id: str) -> list[dict[str, Any]]:
        return self.data["history"].get(canonical_id, [])

    def upsert(self, notice: Notice) -> tuple[bool, list[dict[str, str]]]:
        existing = self.get(notice.canonical_id)
        now = _now()
        if existing is None:
            notice.first_seen = notice.first_seen or now
            notice.last_seen = now
            self.data["notices"][notice.canonical_id] = notice.to_dict()
            self.data["history"].setdefault(notice.canonical_id, []).append(
                {"changed_at": now, "change_type": CHANGE_NEW, "field": "", "old_value": "", "new_value": notice.title}
            )
            self._flush()
            return True, [{"type": CHANGE_NEW, "field": "", "old": "", "new": notice.title}]
        notice.first_seen = existing.get("first_seen") or now
        notice.last_seen = now
        for url in existing.get("attachment_urls") or []:
            if url not in notice.attachment_urls:
                notice.attachment_urls.append(url)
        changes = _diff(existing, notice)
        self.data["notices"][notice.canonical_id] = notice.to_dict()
        for ch in changes:
            self.data["history"].setdefault(notice.canonical_id, []).append({
                "changed_at": now, "change_type": ch["type"], "field": ch["field"],
                "old_value": ch["old"], "new_value": ch["new"],
            })
        self._flush()
        return False, changes

    def record_run(self, summary: dict[str, Any]) -> None:
        self.data["runs"].append({"started_at": _now(), "summary": summary})
        self._flush()

    def _flush(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), "utf-8")

    def close(self) -> None:
        self._flush()


class MemoryStore(JsonStore):
    """Хранилище в памяти — используется для --dry-run и тестов."""

    def __init__(self, seed: Optional[Iterable[Notice]] = None):
        self.path = Path("/dev/null")
        self.data = {"notices": {}, "history": {}, "runs": []}
        for n in seed or []:
            self.data["notices"][n.canonical_id] = n.to_dict()

    def _flush(self) -> None:  # ничего не пишем на диск
        return

    def close(self) -> None:
        return


def open_store(kind: str, path: str | Path) -> TenderStore:
    if kind == "sqlite":
        return SqliteStore(path)
    if kind == "json":
        return JsonStore(path)
    if kind == "memory":
        return MemoryStore()
    raise ValueError(f"unknown store kind: {kind}")
