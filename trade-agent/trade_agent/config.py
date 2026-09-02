"""
Конфигурация системы.

Секреты читаются только из переменных окружения / .env и никогда
не логируются и не выводятся. Файл .env в Git не попадает.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# trade_agent/config.py -> trade_agent -> trade-agent
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent

SECRET_KEYS = (
    "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION",
)


def load_dotenv(path: Optional[Path] = None) -> None:
    """
    Минимальный загрузчик .env без внешних зависимостей.
    Существующие переменные окружения имеют приоритет и не перетираются.
    Содержимое файла никогда не печатается.
    """
    path = path or (PROJECT_DIR / ".env")
    if not path.exists():
        return
    try:
        for line in path.read_text("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        return


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass
class LLMSettings:
    provider: str = "anthropic"
    api_key: str = ""
    base_url: str = "https://api.anthropic.com"
    model_fast: str = "claude-haiku-4-5-20251001"     # Scout — дешёвая сортировка
    model_deep: str = "claude-sonnet-5"               # Analyst / Reviewer
    # Для DeepSeek рассуждение задаётся явно по роли. Для Anthropic None
    # сохраняет прежнее поведение без дополнительного поля thinking.
    thinking_fast: Optional[bool] = None
    thinking_deep: Optional[bool] = None
    max_input_chars: int = 24000
    max_output_tokens: int = 2000
    timeout: float = 90.0
    retries: int = 3
    retry_backoff: float = 4.0
    max_calls_per_run: int = 200
    log_usage: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass
class TelegramBotSettings:
    token: str = ""
    allowed_user_ids: tuple[int, ...] = ()
    # Необязательное дополнительное ограничение: если задано, бот отвечает
    # только в этих чатах. Пусто — разрешён личный чат владельца.
    allowed_chat_ids: tuple[int, ...] = ()
    poll_timeout: int = 30
    # Файл, где хранится offset. Пишется атомарно, чтобы после перезапуска
    # бот не обрабатывал старые обновления заново.
    offset_path: Optional[Path] = None
    # Максимум обновлений за один вызов getUpdates.
    updates_limit: int = 20

    @property
    def configured(self) -> bool:
        return bool(self.token and self.allowed_user_ids)


@dataclass
class Settings:
    project_dir: Path = PROJECT_DIR
    db_path: Path = PROJECT_DIR / "data" / "trade_agent.db"
    brain_dir: Path = PROJECT_DIR / "brain"
    digest_dir: Path = PROJECT_DIR / "digest"
    tenders_dir: Path = PROJECT_DIR / "tenders"
    telegram_dir: Path = PROJECT_DIR / "telegram"
    log_dir: Path = PROJECT_DIR / "logs"

    # Пороги конвейера
    scout_min_score: int = 2          # ниже — шум, в Analyst не идёт
    analyst_min_score: int = 3        # глубокий анализ только с этого уровня
    reviewer_max_revisions: int = 2   # защита от бесконечного цикла
    radar_min_match_score: int = 2
    digest_lookback_days: int = 1
    digest_min_confidence: float = 0.4
    digest_max_per_section: int = 8
    fetch_days: int = 7

    llm: LLMSettings = field(default_factory=LLMSettings)
    bot: TelegramBotSettings = field(default_factory=TelegramBotSettings)
    dry_run: bool = False

    def ensure_dirs(self) -> None:
        for path in (self.db_path.parent, self.digest_dir, self.digest_dir / "archive", self.log_dir):
            path.mkdir(parents=True, exist_ok=True)

    def public_dict(self) -> dict[str, Any]:
        """Безопасное представление настроек: без ключей и токенов."""
        return {
            "project_dir": str(self.project_dir),
            "db_path": str(self.db_path),
            "scout_min_score": self.scout_min_score,
            "analyst_min_score": self.analyst_min_score,
            "reviewer_max_revisions": self.reviewer_max_revisions,
            "llm_provider": self.llm.provider,
            "llm_configured": self.llm.configured,
            "llm_model_fast": self.llm.model_fast,
            "llm_model_deep": self.llm.model_deep,
            "llm_thinking_fast": self.llm.thinking_fast,
            "llm_thinking_deep": self.llm.thinking_deep,
            "bot_configured": self.bot.configured,
            "allowed_users": len(self.bot.allowed_user_ids),
            "allowed_chats": len(self.bot.allowed_chat_ids),
        }


def _allowed_ids(name: str = "TELEGRAM_ALLOWED_USER_ID") -> tuple[int, ...]:
    raw = os.environ.get(name, "")
    ids: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            ids.append(int(chunk))
    return tuple(ids)


def load_settings(project_dir: Optional[Path] = None) -> Settings:
    load_dotenv()
    base = Path(project_dir).resolve() if project_dir else PROJECT_DIR
    provider_name = (os.environ.get("LLM_PROVIDER", "anthropic") or "anthropic").strip().lower()
    api_key_name = "DEEPSEEK_API_KEY" if provider_name == "deepseek" else "ANTHROPIC_API_KEY"
    base_url_name = "DEEPSEEK_BASE_URL" if provider_name == "deepseek" else "ANTHROPIC_BASE_URL"
    default_base_url = (
        "https://api.deepseek.com/anthropic"
        if provider_name == "deepseek" else "https://api.anthropic.com"
    )
    default_model_fast = (
        "deepseek-v4-flash" if provider_name == "deepseek"
        else "claude-haiku-4-5-20251001"
    )
    default_model_deep = (
        "deepseek-v4-pro" if provider_name == "deepseek"
        else "claude-sonnet-5"
    )
    settings = Settings(
        project_dir=base,
        db_path=Path(os.environ.get("TRADE_AGENT_DB") or (base / "data" / "trade_agent.db")),
        brain_dir=base / "brain",
        digest_dir=base / "digest",
        tenders_dir=base / "tenders",
        telegram_dir=base / "telegram",
        log_dir=base / "logs",
        scout_min_score=_int("SCOUT_MIN_SCORE", 2),
        analyst_min_score=_int("ANALYST_MIN_SCORE", 3),
        reviewer_max_revisions=_int("REVIEWER_MAX_REVISIONS", 2),
        radar_min_match_score=_int("RADAR_MIN_MATCH_SCORE", 2),
        digest_lookback_days=_int("DIGEST_LOOKBACK_DAYS", 1),
        digest_min_confidence=_float("DIGEST_MIN_CONFIDENCE", 0.4),
        digest_max_per_section=_int("DIGEST_MAX_PER_SECTION", 8),
        fetch_days=_int("FETCH_DAYS", 7),
        dry_run=_bool("TRADE_AGENT_DRY_RUN", False),
        llm=LLMSettings(
            provider=provider_name,
            api_key=os.environ.get(api_key_name, ""),
            base_url=os.environ.get(base_url_name, default_base_url),
            model_fast=os.environ.get("LLM_MODEL_FAST", default_model_fast),
            model_deep=os.environ.get("LLM_MODEL_DEEP", default_model_deep),
            thinking_fast=(
                _bool("LLM_THINKING_FAST") if provider_name == "deepseek" else None
            ),
            thinking_deep=(
                _bool("LLM_THINKING_DEEP", True) if provider_name == "deepseek" else None
            ),
            max_input_chars=_int("LLM_MAX_INPUT_CHARS", 24000),
            max_output_tokens=_int("LLM_MAX_OUTPUT_TOKENS", 2000),
            timeout=_float("LLM_TIMEOUT", 90.0),
            retries=_int("LLM_RETRIES", 3),
            retry_backoff=_float("LLM_RETRY_BACKOFF", 4.0),
            max_calls_per_run=_int("LLM_MAX_CALLS_PER_RUN", 200),
            log_usage=_bool("LLM_LOG_USAGE", True),
        ),
        bot=TelegramBotSettings(
            token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            allowed_user_ids=_allowed_ids("TELEGRAM_ALLOWED_USER_ID"),
            allowed_chat_ids=_allowed_ids("TELEGRAM_ALLOWED_CHAT_ID"),
            poll_timeout=_int("TELEGRAM_POLL_TIMEOUT", 30),
            offset_path=Path(os.environ.get("TELEGRAM_OFFSET_FILE")
                             or (base / "data" / "bot_offset.json")),
            updates_limit=_int("TELEGRAM_UPDATES_LIMIT", 20),
        ),
    )
    return settings


def redact(text: str) -> str:
    """Убирает из строки значения известных секретов перед логированием."""
    result = text
    for key in SECRET_KEYS:
        value = os.environ.get(key)
        if value and len(value) > 6:
            result = result.replace(value, f"<{key}:redacted>")
    return result
