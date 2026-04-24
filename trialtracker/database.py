from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any, Iterable, Optional

import aiosqlite

from trialtracker.models import TrialDraft


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON")

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def initialize(self) -> None:
        conn = self._require_conn()
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL UNIQUE,
                username TEXT,
                first_name TEXT,
                language_code TEXT,
                timezone TEXT,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_sessions (
                telegram_user_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                service_name TEXT NOT NULL,
                service_key_normalized TEXT NOT NULL,
                raw_input TEXT NOT NULL,
                amount_minor INTEGER NOT NULL,
                currency_code TEXT NOT NULL,
                started_at TEXT NOT NULL,
                billing_at TEXT NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                snooze_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reminder_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trial_id INTEGER NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
                job_type TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                sent_at TEXT,
                status TEXT NOT NULL,
                telegram_message_id INTEGER,
                retry_count INTEGER NOT NULL DEFAULT 0,
                claimed_at TEXT,
                error_text TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                trial_id INTEGER,
                event_name TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_trials_user_status
                ON trials (user_id, status, billing_at);

            CREATE INDEX IF NOT EXISTS idx_trials_service_key
                ON trials (user_id, service_key_normalized, status);

            CREATE INDEX IF NOT EXISTS idx_reminder_jobs_due
                ON reminder_jobs (status, scheduled_at, claimed_at);
            """
        )
        await conn.commit()

    async def upsert_user(self, telegram_user: Any, now_iso: str) -> dict:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT INTO users (
                telegram_user_id, username, first_name, language_code, created_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                language_code=excluded.language_code,
                last_seen_at=excluded.last_seen_at
            """,
            (
                telegram_user.id,
                getattr(telegram_user, "username", None),
                getattr(telegram_user, "first_name", None),
                getattr(telegram_user, "language_code", None),
                now_iso,
                now_iso,
            ),
        )
        await conn.commit()
        row = await self.fetch_user_by_telegram_id(telegram_user.id)
        if row is None:
            raise RuntimeError("Failed to load user after upsert")
        return row

    async def fetch_user_by_telegram_id(self, telegram_user_id: int) -> Optional[dict]:
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT * FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def set_timezone(self, telegram_user_id: int, timezone_name: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            "UPDATE users SET timezone = ? WHERE telegram_user_id = ?",
            (timezone_name, telegram_user_id),
        )
        await conn.commit()

    async def get_session(self, telegram_user_id: int) -> Optional[dict]:
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT state, payload_json, updated_at FROM user_sessions WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "state": row["state"],
            "payload": json.loads(row["payload_json"]),
            "updated_at": row["updated_at"],
        }

    async def set_session(self, telegram_user_id: int, state: str, payload: dict, now_iso: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT INTO user_sessions (telegram_user_id, state, payload_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                state=excluded.state,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (telegram_user_id, state, json.dumps(payload), now_iso),
        )
        await conn.commit()

    async def clear_session(self, telegram_user_id: int) -> None:
        conn = self._require_conn()
        await conn.execute(
            "DELETE FROM user_sessions WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        await conn.commit()

    async def find_active_duplicate(self, user_id: int, service_key: str) -> Optional[dict]:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT * FROM trials
            WHERE user_id = ?
              AND service_key_normalized = ?
              AND status IN ('active', 'reminder_sent')
            ORDER BY billing_at ASC
            LIMIT 1
            """,
            (user_id, service_key),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def create_trial(self, user_id: int, draft: TrialDraft, now_iso: str) -> int:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            INSERT INTO trials (
                user_id, service_name, service_key_normalized, raw_input, amount_minor, currency_code,
                started_at, billing_at, status, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 'manual_text', ?, ?)
            """,
            (
                user_id,
                draft.service_name,
                draft.service_key_normalized,
                draft.raw_input,
                draft.amount_minor,
                draft.currency_code,
                draft.started_at,
                draft.billing_at,
                now_iso,
                now_iso,
            ),
        )
        await conn.commit()
        return int(cursor.lastrowid)

    async def archive_trial(self, trial_id: int, now_iso: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            "UPDATE trials SET status = 'archived', updated_at = ? WHERE id = ?",
            (now_iso, trial_id),
        )
        await self.cancel_pending_jobs(trial_id)
        await conn.commit()

    async def get_trial(self, trial_id: int) -> Optional[dict]:
        conn = self._require_conn()
        cursor = await conn.execute("SELECT * FROM trials WHERE id = ?", (trial_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def schedule_reminder(self, trial_id: int, job_type: str, scheduled_at: str, now_iso: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT INTO reminder_jobs (
                trial_id, job_type, scheduled_at, status, created_at
            ) VALUES (?, ?, ?, 'pending', ?)
            """,
            (trial_id, job_type, scheduled_at, now_iso),
        )
        await conn.commit()

    async def cancel_pending_jobs(self, trial_id: int) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            UPDATE reminder_jobs
            SET status = 'canceled'
            WHERE trial_id = ? AND status = 'pending'
            """,
            (trial_id,),
        )

    async def mark_trial_status(self, trial_id: int, status: str, now_iso: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            "UPDATE trials SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso, trial_id),
        )
        await conn.commit()

    async def increment_snooze_count(self, trial_id: int) -> None:
        conn = self._require_conn()
        await conn.execute(
            "UPDATE trials SET snooze_count = snooze_count + 1 WHERE id = ?",
            (trial_id,),
        )
        await conn.commit()

    async def list_active_trials(self, user_id: int) -> list[dict]:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT * FROM trials
            WHERE user_id = ?
              AND status IN ('active', 'reminder_sent')
            ORDER BY billing_at ASC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_next_upcoming_trial(self, user_id: int) -> Optional[dict]:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT * FROM trials
            WHERE user_id = ?
              AND status IN ('active', 'reminder_sent')
            ORDER BY billing_at ASC
            LIMIT 1
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_saved_totals(self, user_id: int) -> list[dict]:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT currency_code, SUM(amount_minor) AS total_minor
            FROM trials
            WHERE user_id = ?
              AND status = 'canceled_confirmed'
            GROUP BY currency_code
            ORDER BY currency_code ASC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def count_active_trials(self, user_id: int) -> int:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM trials
            WHERE user_id = ?
              AND status IN ('active', 'reminder_sent')
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        return int(row["total"]) if row else 0

    async def expire_overdue_trials(self, now_iso: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            UPDATE trials
            SET status = 'expired_no_response', updated_at = ?
            WHERE status IN ('active', 'reminder_sent')
              AND billing_at < ?
            """,
            (now_iso, now_iso),
        )
        await conn.commit()

    async def claim_due_jobs(self, now_iso: str, batch_size: int) -> list[dict]:
        conn = self._require_conn()
        stale_claim_iso = (datetime.fromisoformat(now_iso) - timedelta(minutes=10)).isoformat()
        await conn.execute("BEGIN")
        cursor = await conn.execute(
            """
            SELECT reminder_jobs.id
            FROM reminder_jobs
            JOIN trials ON trials.id = reminder_jobs.trial_id
            WHERE reminder_jobs.status = 'pending'
              AND reminder_jobs.scheduled_at <= ?
              AND (
                    reminder_jobs.claimed_at IS NULL
                    OR reminder_jobs.claimed_at < ?
                  )
              AND trials.status IN ('active', 'reminder_sent')
            ORDER BY reminder_jobs.scheduled_at ASC
            LIMIT ?
            """,
            (now_iso, stale_claim_iso, batch_size),
        )
        job_rows = await cursor.fetchall()
        job_ids = [row["id"] for row in job_rows]
        if not job_ids:
            await conn.commit()
            return []

        placeholders = ",".join("?" for _ in job_ids)
        await conn.execute(
            f"UPDATE reminder_jobs SET claimed_at = ? WHERE id IN ({placeholders})",
            (now_iso, *job_ids),
        )

        cursor = await conn.execute(
            f"""
            SELECT reminder_jobs.*, trials.service_name, trials.amount_minor, trials.currency_code,
                   trials.billing_at, trials.status AS trial_status, trials.user_id,
                   users.telegram_user_id, users.timezone
            FROM reminder_jobs
            JOIN trials ON trials.id = reminder_jobs.trial_id
            JOIN users ON users.id = trials.user_id
            WHERE reminder_jobs.id IN ({placeholders})
            ORDER BY reminder_jobs.scheduled_at ASC
            """,
            tuple(job_ids),
        )
        rows = await cursor.fetchall()
        await conn.commit()
        return [dict(row) for row in rows]

    async def mark_job_sent(self, job_id: int, telegram_message_id: Optional[int], now_iso: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            UPDATE reminder_jobs
            SET status = 'sent', sent_at = ?, telegram_message_id = ?, claimed_at = NULL, error_text = NULL
            WHERE id = ?
            """,
            (now_iso, telegram_message_id, job_id),
        )
        await conn.commit()

    async def reschedule_job(self, job_id: int, next_time_iso: str, error_text: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            UPDATE reminder_jobs
            SET retry_count = retry_count + 1, scheduled_at = ?, claimed_at = NULL, error_text = ?
            WHERE id = ?
            """,
            (next_time_iso, error_text, job_id),
        )
        await conn.commit()

    async def mark_job_failed(self, job_id: int, error_text: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            UPDATE reminder_jobs
            SET status = 'failed', claimed_at = NULL, error_text = ?
            WHERE id = ?
            """,
            (error_text, job_id),
        )
        await conn.commit()

    async def record_event(
        self,
        user_id: Optional[int],
        trial_id: Optional[int],
        event_name: str,
        payload: dict,
        now_iso: str,
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT INTO event_log (user_id, trial_id, event_name, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, trial_id, event_name, json.dumps(payload), now_iso),
        )
        await conn.commit()

    async def get_user_by_trial(self, trial_id: int) -> Optional[dict]:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT users.*
            FROM users
            JOIN trials ON trials.user_id = users.id
            WHERE trials.id = ?
            """,
            (trial_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    def _require_conn(self) -> aiosqlite.Connection:
        if self.conn is None:
            raise RuntimeError("Database connection is not initialized")
        return self.conn
