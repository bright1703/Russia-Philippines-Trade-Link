"""
Единая база SQLite.

Гарантии:
  * атомарные записи (транзакция на операцию, WAL);
  * идемпотентность — повторный запуск не создаёт дубли
    (UNIQUE-ключи на hash сырья, на сигнал, на пару компания+сигнал);
  * сырьё сохраняется до обработки, поэтому недоступность LLM
    не приводит к потере данных: элементы остаются в очереди.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .models import (
    Analysis, Company, Delivery, Issue, IssueItem, Match, RawItem, Review,
    RunLog, Signal, SourceState, SIGNAL_ANALYZED, SIGNAL_NEW, utcnow,
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS raw_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    source_type  TEXT NOT NULL,
    source_url   TEXT,
    external_id  TEXT,
    title        TEXT,
    raw_text     TEXT,
    published_at TEXT,
    fetched_at   TEXT NOT NULL,
    hash         TEXT NOT NULL UNIQUE,
    meta         TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_source ON raw_items(source, published_at);

CREATE TABLE IF NOT EXISTS signals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_item_id         INTEGER NOT NULL UNIQUE REFERENCES raw_items(id) ON DELETE CASCADE,
    category            TEXT,
    relevance_score     INTEGER DEFAULT 0,
    reason              TEXT,
    companies_matched   TEXT,
    hs_codes            TEXT,
    matched_products    TEXT,
    geography           TEXT,
    needs_deep_analysis INTEGER DEFAULT 0,
    must_alert          INTEGER DEFAULT 0,
    status              TEXT DEFAULT 'new',
    review_attempts     INTEGER DEFAULT 0,
    last_error          TEXT DEFAULT '',
    created_at          TEXT NOT NULL,
    trade_direction     TEXT DEFAULT 'UNKNOWN',
    event_type          TEXT DEFAULT 'OTHER',
    sectors             TEXT,
    jurisdiction        TEXT DEFAULT '',
    destination_market  TEXT DEFAULT '',
    origin_countries    TEXT,
    evidence_fragments  TEXT,
    event_key           TEXT DEFAULT '',
    event_date          TEXT DEFAULT '',
    effective_from      TEXT DEFAULT '',
    deadline            TEXT DEFAULT '',
    first_seen_at       TEXT DEFAULT '',
    verification_status TEXT DEFAULT 'source_claim',
    uncertainties       TEXT,
    next_retry_at       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status, relevance_score);
CREATE INDEX IF NOT EXISTS idx_signals_event ON signals(event_key);

CREATE TABLE IF NOT EXISTS analyses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id         INTEGER NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    company           TEXT,
    summary           TEXT,
    opportunity       TEXT,
    risks             TEXT,
    regulation        TEXT,
    market_data       TEXT,
    suggested_actions TEXT,
    what_to_verify    TEXT,
    next_step         TEXT,
    confidence        REAL DEFAULT 0,
    sources           TEXT,
    revision          INTEGER DEFAULT 0,
    created_at        TEXT NOT NULL,
    UNIQUE(signal_id, revision)
);

CREATE TABLE IF NOT EXISTS reviews (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id      INTEGER NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    verdict          TEXT NOT NULL,
    problems         TEXT,
    corrected_fields TEXT,
    confidence       REAL DEFAULT 0,
    error            TEXT DEFAULT '',
    retryable        INTEGER DEFAULT 0,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    slug              TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    website           TEXT,
    products          TEXT,
    product_aliases   TEXT,
    hs_codes          TEXT,
    categories        TEXT,
    description       TEXT,
    inn               TEXT,
    export_countries  TEXT,
    industry          TEXT,
    contact_name      TEXT,
    address           TEXT,
    contacts          TEXT,
    source_name       TEXT,
    source_row        INTEGER DEFAULT 0,
    data_quality      TEXT,
    sectors           TEXT,
    sector_basis      TEXT,
    roles             TEXT,
    export_experience TEXT,
    documents         TEXT,
    status            TEXT,
    restrictions      TEXT,
    potential_buyers  TEXT,
    regulators        TEXT,
    history           TEXT,
    next_step         TEXT,
    region            TEXT,
    profile_path      TEXT,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    company_slug       TEXT NOT NULL,
    signal_id          INTEGER NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    match_score        INTEGER DEFAULT 0,
    reason             TEXT,
    recommended_action TEXT,
    created_at         TEXT NOT NULL,
    link_type          TEXT DEFAULT 'sector',
    evidence           TEXT,
    role               TEXT DEFAULT 'unknown',
    UNIQUE(company_slug, signal_id)
);
CREATE INDEX IF NOT EXISTS idx_matches_score ON matches(match_score, created_at);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stage        TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT,
    processed    INTEGER DEFAULT 0,
    created      INTEGER DEFAULT 0,
    skipped      INTEGER DEFAULT 0,
    errors       INTEGER DEFAULT 0,
    retries      INTEGER DEFAULT 0,
    duration_sec REAL DEFAULT 0,
    error_text   TEXT,
    details      TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_stage ON runs(stage, started_at);

-- Состав выпуска фиксируется один раз. Карточки, счётчики, список компаний
-- и посты в Telegram строятся из одного сохранённого набора issue_items,
-- поэтому шапка не может противоречить содержимому.
CREATE TABLE IF NOT EXISTS issues (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL DEFAULT 'scheduled',
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    built_at      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'built',
    coverage      TEXT,
    counters      TEXT,
    markdown_path TEXT DEFAULT '',
    content_hash  TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_issues_built ON issues(built_at);

CREATE TABLE IF NOT EXISTS issue_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id            INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    position            INTEGER NOT NULL DEFAULT 0,
    section             TEXT NOT NULL,
    event_key           TEXT DEFAULT '',
    signal_id           INTEGER,
    analysis_id         INTEGER,
    title               TEXT,
    fact                TEXT,
    meaning             TEXT,
    trade_direction     TEXT DEFAULT 'UNKNOWN',
    event_type          TEXT DEFAULT 'OTHER',
    sectors             TEXT,
    source_urls         TEXT,
    published_at        TEXT DEFAULT '',
    verification_status TEXT DEFAULT 'source_claim',
    late_confirmation   INTEGER DEFAULT 0,
    urgent              INTEGER DEFAULT 0,
    action              TEXT DEFAULT '',
    companies_direct    TEXT,
    companies_sector    TEXT,
    companies_total     INTEGER DEFAULT 0,
    created_at          TEXT NOT NULL,
    UNIQUE(issue_id, event_key)
);
CREATE INDEX IF NOT EXISTS idx_issue_items_issue ON issue_items(issue_id, position);

-- Доставка привязана к конкретному собранному выпуску. Подтверждённая
-- доставка не повторяется, неопределённая не считается успешной.
CREATE TABLE IF NOT EXISTS deliveries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id    INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL DEFAULT 'telegram',
    chat_id     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'unknown',
    parts_total INTEGER DEFAULT 0,
    parts_sent  INTEGER DEFAULT 0,
    message_ids TEXT,
    error       TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(issue_id, channel, chat_id)
);

-- Позиция каждого источника. Молчание исправного источника, ошибка
-- загрузки и неподключённый источник — разные состояния.
CREATE TABLE IF NOT EXISTS source_state (
    source_id         TEXT PRIMARY KEY,
    last_success_at   TEXT DEFAULT '',
    last_published_at TEXT DEFAULT '',
    cursor            TEXT DEFAULT '',
    coverage_complete INTEGER DEFAULT 1,
    last_error        TEXT DEFAULT '',
    items_last_run    INTEGER DEFAULT 0,
    new_last_run      INTEGER DEFAULT 0,
    updated_at        TEXT NOT NULL
);
"""


def _json_text(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(0, days))).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    # -- миграции ---------------------------------------------------------
    MIGRATIONS = (
        ("reviews", "error", "TEXT DEFAULT ''"),
        ("reviews", "retryable", "INTEGER DEFAULT 0"),
        ("signals", "review_attempts", "INTEGER DEFAULT 0"),
        ("signals", "last_error", "TEXT DEFAULT ''"),
        ("signals", "matched_products", "TEXT"),
        ("signals", "must_alert", "INTEGER DEFAULT 0"),
        ("companies", "product_aliases", "TEXT"),
        ("companies", "description", "TEXT"),
        ("companies", "inn", "TEXT"),
        ("companies", "export_countries", "TEXT"),
        ("companies", "industry", "TEXT"),
        ("companies", "contact_name", "TEXT"),
        ("companies", "address", "TEXT"),
        ("companies", "contacts", "TEXT"),
        ("companies", "source_name", "TEXT"),
        ("companies", "source_row", "INTEGER DEFAULT 0"),
        ("companies", "data_quality", "TEXT"),
        ("companies", "sectors", "TEXT"),
        ("companies", "sector_basis", "TEXT"),
        ("companies", "roles", "TEXT"),
        ("signals", "trade_direction", "TEXT DEFAULT 'UNKNOWN'"),
        ("signals", "event_type", "TEXT DEFAULT 'OTHER'"),
        ("signals", "sectors", "TEXT"),
        ("signals", "jurisdiction", "TEXT DEFAULT ''"),
        ("signals", "destination_market", "TEXT DEFAULT ''"),
        ("signals", "origin_countries", "TEXT"),
        ("signals", "evidence_fragments", "TEXT"),
        ("signals", "event_key", "TEXT DEFAULT ''"),
        ("signals", "event_date", "TEXT DEFAULT ''"),
        ("signals", "effective_from", "TEXT DEFAULT ''"),
        ("signals", "deadline", "TEXT DEFAULT ''"),
        ("signals", "first_seen_at", "TEXT DEFAULT ''"),
        ("signals", "verification_status", "TEXT DEFAULT 'source_claim'"),
        ("signals", "uncertainties", "TEXT"),
        ("signals", "next_retry_at", "TEXT DEFAULT ''"),
        ("matches", "link_type", "TEXT DEFAULT 'sector'"),
        ("matches", "evidence", "TEXT"),
        ("matches", "role", "TEXT DEFAULT 'unknown'"),
    )

    def _migrate(self) -> None:
        """Добавляет недостающие колонки в уже существующей базе."""
        for table, column, definition in self.MIGRATIONS:
            existing = {row["name"] for row in
                        self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    # -- служебное --------------------------------------------------------
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Атомарная транзакция: либо всё, либо ничего."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    @staticmethod
    def _insert_sql(table: str, row: dict[str, Any], or_clause: str = "") -> tuple[str, list[Any]]:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        return f"INSERT {or_clause} INTO {table} ({cols}) VALUES ({marks})", list(row.values())

    # -- raw_items --------------------------------------------------------
    def upsert_raw_item(self, item: RawItem) -> tuple[int, bool]:
        """
        Идемпотентная вставка по hash. Возвращает (id, создано_ли).
        Повторный запуск того же сбора дублей не создаёт.
        """
        row = item.to_row()
        with self.tx() as conn:
            existing = conn.execute(
                "SELECT id FROM raw_items WHERE hash = ?", (item.hash,)
            ).fetchone()
            if existing:
                return int(existing["id"]), False
            sql, values = self._insert_sql("raw_items", row)
            cursor = conn.execute(sql, values)
            return int(cursor.lastrowid), True

    def get_raw_item(self, raw_id: int) -> Optional[RawItem]:
        row = self.conn.execute("SELECT * FROM raw_items WHERE id = ?", (raw_id,)).fetchone()
        return RawItem.from_row(row) if row else None

    def raw_items_without_signal(self, limit: int = 200) -> list[RawItem]:
        """Очередь для Scout. Материал остаётся здесь, пока не обработан."""
        rows = self.conn.execute(
            "SELECT r.* FROM raw_items r "
            "LEFT JOIN signals s ON s.raw_item_id = r.id "
            "WHERE s.id IS NULL ORDER BY r.id LIMIT ?", (limit,)
        ).fetchall()
        return [RawItem.from_row(r) for r in rows]

    def count_raw_items(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) c FROM raw_items").fetchone()["c"])

    # -- signals ----------------------------------------------------------
    def upsert_signal(self, signal: Signal) -> tuple[int, bool]:
        row = signal.to_row()
        with self.tx() as conn:
            existing = conn.execute(
                "SELECT id FROM signals WHERE raw_item_id = ?", (signal.raw_item_id,)
            ).fetchone()
            if existing:
                return int(existing["id"]), False
            sql, values = self._insert_sql("signals", row)
            cursor = conn.execute(sql, values)
            return int(cursor.lastrowid), True

    def get_signal(self, signal_id: int) -> Optional[Signal]:
        row = self.conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        return Signal.from_row(row) if row else None

    def signals_for_analysis(self, min_score: int, limit: int = 50,
                             max_attempts: int = 3) -> list[Signal]:
        """
        Очередь на анализ. Сигналы, исчерпавшие попытки рецензии,
        сюда не попадают — иначе получится дорогой бесконечный цикл.
        """
        rows = self.conn.execute(
            "SELECT * FROM signals WHERE status = ? AND relevance_score >= ? "
            "AND COALESCE(review_attempts, 0) < ? "
            "AND COALESCE(next_retry_at, '') <= ? "
            "ORDER BY relevance_score DESC, id LIMIT ?",
            (SIGNAL_NEW, min_score, max_attempts, utcnow(), limit),
        ).fetchall()
        return [Signal.from_row(r) for r in rows]

    def schedule_retry(self, signal_id: int, minutes: int, error: str = "") -> str:
        """
        Откладывает следующую попытку.

        Без паузы сигнал с ошибкой модели возвращается в очередь в том же
        запуске и сжигает лимит вызовов на одном и том же материале.
        """
        moment = (datetime.now(timezone.utc)
                  + timedelta(minutes=max(1, minutes))).isoformat(timespec="seconds")
        with self.tx() as conn:
            conn.execute("UPDATE signals SET next_retry_at = ?, last_error = ? WHERE id = ?",
                         (moment, error, signal_id))
        return moment

    def signals_since(self, days: int, min_score: int = 0) -> list[Signal]:
        rows = self.conn.execute(
            "SELECT * FROM signals WHERE created_at >= ? AND relevance_score >= ? "
            "ORDER BY relevance_score DESC, id DESC",
            (_iso_days_ago(days), min_score),
        ).fetchall()
        return [Signal.from_row(r) for r in rows]

    def signals_by_ids(self, signal_ids: Iterable[int]) -> dict[int, Signal]:
        """
        Сигналы по идентификаторам — без фильтра по времени.

        Нужно там, где анализ завершился сегодня, а сигнал создан раньше
        окна выпуска. Разные временные фильтры для сигнала и анализа
        разрывали связь новости, анализа и компаний.
        """
        ids = [int(value) for value in signal_ids if value]
        result: dict[int, Signal] = {}
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            marks = ", ".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT * FROM signals WHERE id IN ({marks})", chunk).fetchall()
            for row in rows:
                signal = Signal.from_row(row)
                result[int(signal.id or 0)] = signal
        return result

    def update_signal_fields(self, signal_id: int, **fields: Any) -> None:
        """Точечное обновление сигнала (event_key, направление, проверка)."""
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self.tx() as conn:
            conn.execute(f"UPDATE signals SET {assignments} WHERE id = ?",
                         [*fields.values(), signal_id])

    def set_signal_status(self, signal_id: int, status: str, error: str = "") -> None:
        with self.tx() as conn:
            conn.execute("UPDATE signals SET status = ?, last_error = ? WHERE id = ?",
                         (status, error, signal_id))

    def bump_review_attempt(self, signal_id: int, error: str = "") -> int:
        """Увеличивает счётчик попыток рецензии. Защита от дорогого зацикливания."""
        with self.tx() as conn:
            conn.execute(
                "UPDATE signals SET review_attempts = COALESCE(review_attempts, 0) + 1, "
                "last_error = ? WHERE id = ?", (error, signal_id))
            row = conn.execute("SELECT review_attempts FROM signals WHERE id = ?",
                               (signal_id,)).fetchone()
        return int(row["review_attempts"]) if row else 0

    # -- analyses / reviews -----------------------------------------------
    def insert_analysis(self, analysis: Analysis) -> int:
        row = analysis.to_row()
        with self.tx() as conn:
            sql, values = self._insert_sql("analyses", row, or_clause="OR REPLACE")
            cursor = conn.execute(sql, values)
            return int(cursor.lastrowid)

    def latest_analysis(self, signal_id: int) -> Optional[Analysis]:
        row = self.conn.execute(
            "SELECT * FROM analyses WHERE signal_id = ? ORDER BY revision DESC, id DESC LIMIT 1",
            (signal_id,),
        ).fetchone()
        return Analysis.from_row(row) if row else None

    def analyses_since(self, days: int) -> list[Analysis]:
        rows = self.conn.execute(
            "SELECT a.* FROM analyses a WHERE a.created_at >= ? ORDER BY a.confidence DESC, a.id DESC",
            (_iso_days_ago(days),),
        ).fetchall()
        return [Analysis.from_row(r) for r in rows]

    def insert_review(self, review: Review) -> int:
        with self.tx() as conn:
            sql, values = self._insert_sql("reviews", review.to_row())
            cursor = conn.execute(sql, values)
            return int(cursor.lastrowid)

    def latest_review(self, analysis_id: int) -> Optional[Review]:
        row = self.conn.execute(
            "SELECT * FROM reviews WHERE analysis_id = ? ORDER BY id DESC LIMIT 1", (analysis_id,)
        ).fetchone()
        return Review.from_row(row) if row else None

    def passed_analyses_since(self, days: int) -> list[tuple[Analysis, Review]]:
        rows = self.conn.execute(
            "SELECT a.*, r.verdict AS r_verdict, r.problems AS r_problems, "
            "       r.corrected_fields AS r_corrected, r.confidence AS r_confidence, "
            "       r.id AS r_id, r.created_at AS r_created "
            "FROM analyses a JOIN reviews r ON r.analysis_id = a.id "
            "WHERE r.verdict = 'PASS' AND a.created_at >= ? "
            "ORDER BY a.confidence DESC, a.id DESC",
            (_iso_days_ago(days),),
        ).fetchall()
        result: list[tuple[Analysis, Review]] = []
        for row in rows:
            data = {k: row[k] for k in row.keys() if not k.startswith("r_")}
            analysis = Analysis.from_row(data)
            review = Review.from_row({
                "id": row["r_id"], "analysis_id": analysis.id, "verdict": row["r_verdict"],
                "problems": row["r_problems"], "corrected_fields": row["r_corrected"],
                "confidence": row["r_confidence"], "created_at": row["r_created"],
            })
            result.append((analysis, review))
        return result

    # -- companies --------------------------------------------------------
    # Поля профиля, которые нельзя молча обнулить пустым значением.
    COMPANY_MERGEABLE = (
        "website", "products", "product_aliases", "hs_codes", "categories",
        "description", "inn", "export_countries", "industry", "source_name",
        "contact_name", "address", "contacts", "source_row", "data_quality",
        "sectors", "sector_basis", "roles", "export_experience",
        "documents", "status", "restrictions", "potential_buyers", "regulators",
        "history", "next_step", "region",
    )

    def upsert_company(self, company: Company, mode: str = "merge") -> tuple[int, bool]:
        """
        Сохраняет профиль компании.

        mode="merge" (по умолчанию): пустое входящее поле НЕ затирает
        заполненное в базе. Так обычная синхронизация из brain/companies
        не может потерять товары, HS-коды и категории, импортированные
        из каталога.

        mode="overwrite": полная замена всех полей. Включается только
        явным флагом (--overwrite у импорта).
        """
        if mode not in ("merge", "overwrite"):
            raise ValueError(f"неизвестный режим записи профиля: {mode}")

        row = company.to_row()
        row["updated_at"] = utcnow()
        with self.tx() as conn:
            existing = conn.execute(
                "SELECT * FROM companies WHERE slug = ?", (company.slug,)
            ).fetchone()
            if existing is None:
                sql, values = self._insert_sql("companies", row)
                cursor = conn.execute(sql, values)
                return int(cursor.lastrowid), True

            if mode == "merge":
                current = dict(existing)
                for field_name in self.COMPANY_MERGEABLE:
                    incoming = row.get(field_name)
                    if self._is_empty_value(incoming) and not self._is_empty_value(
                            current.get(field_name)):
                        row[field_name] = current[field_name]
                # Название и путь к профилю тоже не обнуляем.
                for field_name in ("name", "profile_path"):
                    if not str(row.get(field_name) or "").strip() and current.get(field_name):
                        row[field_name] = current[field_name]

            assignments = ", ".join(f"{k} = ?" for k in row)
            conn.execute(
                f"UPDATE companies SET {assignments} WHERE id = ?",
                list(row.values()) + [existing["id"]],
            )
            return int(existing["id"]), False

    @staticmethod
    def _is_empty_value(value: Any) -> bool:
        """Пустая строка, None, пустой JSON-список — считаются пустыми."""
        if value is None:
            return True
        text = str(value).strip()
        return text in ("", "[]", "{}", "null")

    def all_companies(self) -> list[Company]:
        rows = self.conn.execute("SELECT * FROM companies ORDER BY name").fetchall()
        return [Company.from_row(r) for r in rows]

    def get_company(self, slug: str) -> Optional[Company]:
        row = self.conn.execute("SELECT * FROM companies WHERE slug = ?", (slug,)).fetchone()
        return Company.from_row(row) if row else None

    # -- matches ----------------------------------------------------------
    def upsert_match(self, match: Match) -> tuple[int, bool]:
        row = match.to_row()
        with self.tx() as conn:
            existing = conn.execute(
                "SELECT id FROM matches WHERE company_slug = ? AND signal_id = ?",
                (match.company_slug, match.signal_id),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE matches SET match_score = ?, reason = ?, recommended_action = ? WHERE id = ?",
                    (match.match_score, match.reason, match.recommended_action, existing["id"]),
                )
                return int(existing["id"]), False
            sql, values = self._insert_sql("matches", row)
            cursor = conn.execute(sql, values)
            return int(cursor.lastrowid), True

    def matches_for_signals(self, signal_ids: Iterable[int],
                            min_score: int = 0) -> list[Match]:
        """
        Совпадения для конкретных сигналов выпуска.

        Совпадения выбираются по сигналам показанных карточек, а не по
        собственному created_at: иначе счётчик компаний в шапке относится
        к другому набору строк, чем сами карточки.
        """
        ids = [int(value) for value in signal_ids if value]
        result: list[Match] = []
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            marks = ", ".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT * FROM matches WHERE signal_id IN ({marks}) AND match_score >= ? "
                "ORDER BY match_score DESC, id DESC", [*chunk, min_score]).fetchall()
            result.extend(Match.from_row(r) for r in rows)
        return result

    def matches_since(self, days: int, min_score: int = 0) -> list[Match]:
        rows = self.conn.execute(
            "SELECT * FROM matches WHERE created_at >= ? AND match_score >= ? "
            "ORDER BY match_score DESC, id DESC",
            (_iso_days_ago(days), min_score),
        ).fetchall()
        return [Match.from_row(r) for r in rows]

    # -- runs -------------------------------------------------------------
    def start_run(self, stage: str) -> int:
        with self.tx() as conn:
            cursor = conn.execute(
                "INSERT INTO runs (stage, started_at, status) VALUES (?,?,?)",
                (stage, utcnow(), "running"),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, log: RunLog) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE runs SET finished_at = ?, status = ?, processed = ?, created = ?, "
                "skipped = ?, errors = ?, retries = ?, duration_sec = ?, error_text = ?, details = ? "
                "WHERE id = ?",
                (utcnow(), log.status, log.processed, log.created, log.skipped, log.errors,
                 log.retries, log.duration_sec, log.error_text,
                 RunLog.to_row(log)["details"], run_id),
            )

    def recent_runs(self, limit: int = 10, stage: Optional[str] = None) -> list[RunLog]:
        if stage:
            rows = self.conn.execute(
                "SELECT * FROM runs WHERE stage = ? ORDER BY id DESC LIMIT ?", (stage, limit)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [RunLog.from_row(r) for r in rows]

    def count_runs_since(self, stage: str, since: str) -> int:
        """Сколько прогонов этапа было начато с указанного момента."""
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM runs WHERE stage = ? AND started_at >= ?",
            (stage, since)).fetchone()
        return int(row["c"]) if row else 0

    def signals_needing_attention(self, limit: int = 20) -> list[Signal]:
        rows = self.conn.execute(
            "SELECT * FROM signals WHERE status IN ('failed', 'needs_review') "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [Signal.from_row(r) for r in rows]


    # -- выпуски ----------------------------------------------------------
    def create_issue(self, issue: Issue) -> int:
        """Создаёт выпуск. Состав карточек добавляется одной транзакцией."""
        with self.tx() as conn:
            sql, values = self._insert_sql("issues", issue.to_row())
            cursor = conn.execute(sql, values)
            return int(cursor.lastrowid)

    def save_issue_items(self, issue_id: int, items: list[IssueItem]) -> int:
        """
        Сохраняет состав выпуска целиком.

        Либо сохраняются все карточки, либо ни одной: выпуск с половиной
        карточек и счётчиками от полного состава — это ровно та ошибка,
        из-за которой шапка расходилась с содержимым.
        """
        with self.tx() as conn:
            conn.execute("DELETE FROM issue_items WHERE issue_id = ?", (issue_id,))
            for position, item in enumerate(items, start=1):
                item.issue_id = issue_id
                item.position = position
                sql, values = self._insert_sql("issue_items", item.to_row())
                conn.execute(sql, values)
        return len(items)

    def finish_issue(self, issue_id: int, status: str, counters: dict[str, Any],
                     coverage: dict[str, Any], markdown_path: str,
                     content_hash: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE issues SET status = ?, counters = ?, coverage = ?, "
                "markdown_path = ?, content_hash = ? WHERE id = ?",
                (status, _json_text(counters), _json_text(coverage),
                 markdown_path, content_hash, issue_id),
            )

    def get_issue(self, issue_id: int) -> Optional[Issue]:
        row = self.conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
        return Issue.from_row(row) if row else None

    def latest_issue(self, status: str = "built") -> Optional[Issue]:
        """Последний УСПЕШНО собранный выпуск. Сбойный не выдаётся за новый."""
        row = self.conn.execute(
            "SELECT * FROM issues WHERE status = ? ORDER BY id DESC LIMIT 1", (status,)
        ).fetchone()
        return Issue.from_row(row) if row else None

    def issue_items(self, issue_id: int) -> list[IssueItem]:
        rows = self.conn.execute(
            "SELECT * FROM issue_items WHERE issue_id = ? ORDER BY position", (issue_id,)
        ).fetchall()
        return [IssueItem.from_row(r) for r in rows]

    def published_event_keys(self, before_issue_id: Optional[int] = None) -> dict[str, str]:
        """
        Уже опубликованные события: ключ события → дата выпуска.

        Нужно, чтобы повторное событие выходило только при существенном
        обновлении, а не при каждом перепосте.
        """
        sql = ("SELECT i.event_key AS k, MAX(s.built_at) AS built FROM issue_items i "
               "JOIN issues s ON s.id = i.issue_id WHERE i.event_key != ''")
        args: list[Any] = []
        if before_issue_id is not None:
            sql += " AND i.issue_id != ?"
            args.append(before_issue_id)
        sql += " GROUP BY i.event_key"
        return {row["k"]: row["built"] for row in self.conn.execute(sql, args).fetchall()}

    # -- доставка ---------------------------------------------------------
    def get_delivery(self, issue_id: int, chat_id: str,
                     channel: str = "telegram") -> Optional[Delivery]:
        row = self.conn.execute(
            "SELECT * FROM deliveries WHERE issue_id = ? AND channel = ? AND chat_id = ?",
            (issue_id, channel, str(chat_id)),
        ).fetchone()
        return Delivery.from_row(row) if row else None

    def save_delivery(self, delivery: Delivery) -> int:
        row = delivery.to_row()
        row["updated_at"] = utcnow()
        with self.tx() as conn:
            existing = conn.execute(
                "SELECT id FROM deliveries WHERE issue_id = ? AND channel = ? AND chat_id = ?",
                (delivery.issue_id, delivery.channel, str(delivery.chat_id)),
            ).fetchone()
            if existing is None:
                sql, values = self._insert_sql("deliveries", row)
                cursor = conn.execute(sql, values)
                return int(cursor.lastrowid)
            row.pop("created_at", None)
            assignments = ", ".join(f"{k} = ?" for k in row)
            conn.execute(f"UPDATE deliveries SET {assignments} WHERE id = ?",
                         list(row.values()) + [existing["id"]])
            return int(existing["id"])

    def deliveries_for_issue(self, issue_id: int) -> list[Delivery]:
        rows = self.conn.execute(
            "SELECT * FROM deliveries WHERE issue_id = ? ORDER BY id", (issue_id,)
        ).fetchall()
        return [Delivery.from_row(r) for r in rows]

    # -- позиции источников ------------------------------------------------
    def get_source_state(self, source_id: str) -> Optional[SourceState]:
        row = self.conn.execute(
            "SELECT * FROM source_state WHERE source_id = ?", (source_id,)
        ).fetchone()
        return SourceState.from_row(row) if row else None

    def save_source_state(self, state: SourceState) -> None:
        row = state.to_row()
        row["updated_at"] = utcnow()
        with self.tx() as conn:
            existing = conn.execute(
                "SELECT source_id FROM source_state WHERE source_id = ?",
                (state.source_id,)).fetchone()
            if existing is None:
                sql, values = self._insert_sql("source_state", row)
                conn.execute(sql, values)
                return
            payload = {k: v for k, v in row.items() if k != "source_id"}
            assignments = ", ".join(f"{k} = ?" for k in payload)
            conn.execute(f"UPDATE source_state SET {assignments} WHERE source_id = ?",
                         list(payload.values()) + [state.source_id])

    def all_source_states(self) -> list[SourceState]:
        rows = self.conn.execute("SELECT * FROM source_state ORDER BY source_id").fetchall()
        return [SourceState.from_row(r) for r in rows]

    # -- сводка -----------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        def one(sql: str, *args: Any) -> int:
            return int(self.conn.execute(sql, args).fetchone()[0])

        return {
            "raw_items": one("SELECT COUNT(*) FROM raw_items"),
            "queue": one("SELECT COUNT(*) FROM raw_items r LEFT JOIN signals s "
                         "ON s.raw_item_id = r.id WHERE s.id IS NULL"),
            "signals": one("SELECT COUNT(*) FROM signals"),
            "signals_new": one("SELECT COUNT(*) FROM signals WHERE status = ?", SIGNAL_NEW),
            "signals_analyzed": one("SELECT COUNT(*) FROM signals WHERE status = ?", SIGNAL_ANALYZED),
            "signals_failed": one("SELECT COUNT(*) FROM signals WHERE status = ?", "failed"),
            "signals_needs_review": one("SELECT COUNT(*) FROM signals WHERE status = ?",
                                        "needs_review"),
            "analyses": one("SELECT COUNT(*) FROM analyses"),
            "reviews_pass": one("SELECT COUNT(*) FROM reviews WHERE verdict = 'PASS'"),
            "reviews_failed": one("SELECT COUNT(*) FROM reviews WHERE verdict = 'FAILED'"),
            "companies": one("SELECT COUNT(*) FROM companies"),
            "matches": one("SELECT COUNT(*) FROM matches"),
            "runs": one("SELECT COUNT(*) FROM runs"),
            "last_error": (self.conn.execute(
                "SELECT stage || ': ' || COALESCE(error_text,'') FROM runs "
                "WHERE status = 'error' ORDER BY id DESC LIMIT 1"
            ).fetchone() or [""])[0],
        }
