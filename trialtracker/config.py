from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    db_path: Path
    reminder_poll_seconds: int
    reminder_batch_size: int
    log_level: str
    app_timezone: str


def load_settings(require_token: bool = True) -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if require_token and not bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    db_path = Path(os.getenv("DB_PATH", "data/trialdrop.db")).expanduser()
    reminder_poll_seconds = int(os.getenv("REMINDER_POLL_SECONDS", "30"))
    reminder_batch_size = int(os.getenv("REMINDER_BATCH_SIZE", "20"))
    log_level = os.getenv("LOG_LEVEL", "INFO").strip() or "INFO"
    app_timezone = os.getenv("APP_TIMEZONE", "Europe/Berlin").strip() or "Europe/Berlin"

    return Settings(
        bot_token=bot_token,
        db_path=db_path,
        reminder_poll_seconds=reminder_poll_seconds,
        reminder_batch_size=reminder_batch_size,
        log_level=log_level,
        app_timezone=app_timezone,
    )
