from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from trialtracker.config import Settings
from trialtracker.database import Database
from trialtracker.formatting import (
    format_billing_date,
    format_money,
    format_saved_totals,
    format_trial_line,
)
from trialtracker.keyboards import draft_preview_keyboard, duplicate_keyboard, reminder_keyboard
from trialtracker.models import TrialDraft
from trialtracker.parser import parse_date_only, parse_trial_text


logger = logging.getLogger(__name__)


def build_dispatcher(database: Database, settings: Settings) -> Dispatcher:
    dispatcher = Dispatcher()
    router = Router()

    async def ensure_user(message: Message) -> dict:
        return await database.upsert_user(message.from_user, now_utc_iso())

    async def ensure_callback_user(callback: CallbackQuery) -> Optional[dict]:
        if callback.from_user is None:
            return None
        return await database.fetch_user_by_telegram_id(callback.from_user.id)

    async def show_draft_preview(target: Message, user: dict, draft: TrialDraft) -> None:
        await database.set_session(
            telegram_user_id=user["telegram_user_id"],
            state="awaiting_save_confirmation",
            payload={"draft": draft.to_dict()},
            now_iso=now_utc_iso(),
        )
        await target.answer(
            build_preview_text(draft=draft, timezone_name=settings.app_timezone),
            reply_markup=draft_preview_keyboard(),
        )

    async def continue_draft_flow(target: Message, user: dict, draft: TrialDraft) -> None:
        missing = draft.missing_fields()
        if "date" in missing:
            await database.set_session(
                telegram_user_id=user["telegram_user_id"],
                state="awaiting_date",
                payload={"draft": draft.to_dict()},
                now_iso=now_utc_iso(),
            )
            await target.answer(
                "\n".join(
                    [
                        "Не увидел срок trial или дату конца.",
                        "Можешь ответить так:",
                        "14 дней",
                        "2 недели",
                        "полмесяца",
                        "5 мая",
                    ]
                )
            )
            return

        duplicate = await database.find_active_duplicate(user["id"], draft.service_key_normalized)
        if duplicate:
            await database.set_session(
                telegram_user_id=user["telegram_user_id"],
                state="awaiting_duplicate_resolution",
                payload={"draft": draft.to_dict(), "duplicate_trial_id": duplicate["id"]},
                now_iso=now_utc_iso(),
            )
            await target.answer(
                "\n".join(
                    [
                        f"У тебя уже есть активная запись для {duplicate['service_name']}.",
                        "Если это новый trial, можешь заменить старую запись или сохранить обе.",
                    ]
                ),
                reply_markup=duplicate_keyboard(),
            )
            await database.record_event(
                user_id=user["id"],
                trial_id=duplicate["id"],
                event_name="duplicate_detected",
                payload={"service_key": draft.service_key_normalized},
                now_iso=now_utc_iso(),
            )
            return

        await show_draft_preview(target, user, draft)

    async def finalize_trial_save(target: Message, user: dict, draft: TrialDraft) -> None:
        now_iso = now_utc_iso()
        trial_id = await database.create_trial(user_id=user["id"], draft=draft, now_iso=now_iso)
        reminder_at = calculate_primary_reminder(
            billing_at_iso=draft.billing_at,
            now_iso=now_iso,
            timezone_name=settings.app_timezone,
        )
        await database.schedule_reminder(
            trial_id=trial_id,
            job_type="primary",
            scheduled_at=reminder_at,
            now_iso=now_iso,
        )
        await database.clear_session(user["telegram_user_id"])
        await database.record_event(
            user_id=user["id"],
            trial_id=trial_id,
            event_name="trial_saved",
            payload={"service_name": draft.service_name},
            now_iso=now_iso,
        )

        lines = [
            "Сохранил.",
            f"Напомню за день до конца trial: {format_billing_date(reminder_at, settings.app_timezone)}",
        ]
        if draft.amount_minor is None or not draft.currency_code:
            lines.append("Цена не указана. Напомню все равно, но в сэкономленное ее не посчитаю.")
        await target.answer("\n".join(lines))

    @router.message(CommandStart())
    async def start_handler(message: Message) -> None:
        user = await ensure_user(message)
        await database.record_event(
            user_id=user["id"],
            trial_id=None,
            event_name="user_started",
            payload={"source": "command_start"},
            now_iso=now_utc_iso(),
        )
        await message.answer(build_input_help_text())

    @router.message(Command("help"))
    async def help_handler(message: Message) -> None:
        await ensure_user(message)
        await message.answer(build_input_help_text())

    @router.message(Command("add"))
    async def add_handler(message: Message) -> None:
        await ensure_user(message)
        await message.answer(build_input_help_text())

    @router.message(Command("list"))
    async def list_handler(message: Message) -> None:
        user = await ensure_user(message)
        await database.expire_overdue_trials(now_utc_iso())
        trials = await database.list_active_trials(user["id"])
        if not trials:
            await message.answer("Сейчас у тебя нет активных trial.")
            return

        lines = ["Активные trial:"]
        for trial in trials:
            lines.append(f"• {format_trial_line(trial, settings.app_timezone)}")
        await message.answer("\n".join(lines))

    @router.message(Command("stats"))
    async def stats_handler(message: Message) -> None:
        user = await ensure_user(message)
        await database.expire_overdue_trials(now_utc_iso())
        totals = await database.get_saved_totals(user["id"])
        active_count = await database.count_active_trials(user["id"])
        canceled_without_amount = await database.count_canceled_without_amount(user["id"])
        next_trial = await database.get_next_upcoming_trial(user["id"])
        await database.record_event(
            user_id=user["id"],
            trial_id=None,
            event_name="stats_viewed",
            payload={"active_count": active_count},
            now_iso=now_utc_iso(),
        )

        lines = [
            f"Сэкономлено: {format_saved_totals(totals)}",
            f"Активных trial: {active_count}",
        ]
        if canceled_without_amount:
            lines.append(f"Отменено без цены: {canceled_without_amount}")
        if next_trial:
            next_line = f"Ближайший дедлайн: {next_trial['service_name']} — {format_billing_date(next_trial['billing_at'], settings.app_timezone)}"
            if next_trial["amount_minor"] is not None and next_trial["currency_code"]:
                next_line += f" — {format_money(next_trial['amount_minor'], next_trial['currency_code'])}"
            lines.append(next_line)
        await message.answer("\n".join(lines))

    @router.callback_query(F.data == "draft:edit")
    async def draft_edit_handler(callback: CallbackQuery) -> None:
        user = await ensure_callback_user(callback)
        if not user:
            await callback.answer("Не нашел пользователя", show_alert=True)
            return
        await database.clear_session(user["telegram_user_id"])
        await callback.answer("Ок")
        if callback.message:
            await callback.message.answer("Пришли запись еще раз, как тебе удобно.")

    @router.callback_query(F.data == "draft:save")
    async def draft_save_handler(callback: CallbackQuery) -> None:
        user = await ensure_callback_user(callback)
        if not user:
            await callback.answer("Не нашел пользователя", show_alert=True)
            return
        session = await database.get_session(user["telegram_user_id"])
        if not session or session["state"] != "awaiting_save_confirmation":
            await callback.answer("Эта запись уже устарела", show_alert=True)
            return

        draft = TrialDraft.from_dict(session["payload"]["draft"])
        if not draft.is_complete:
            await callback.answer("В записи не хватает срока или даты", show_alert=True)
            return

        duplicate = await database.find_active_duplicate(user["id"], draft.service_key_normalized)
        if duplicate:
            await database.set_session(
                telegram_user_id=user["telegram_user_id"],
                state="awaiting_duplicate_resolution",
                payload={"draft": draft.to_dict(), "duplicate_trial_id": duplicate["id"]},
                now_iso=now_utc_iso(),
            )
            await callback.answer()
            if callback.message:
                await callback.message.answer(
                    "\n".join(
                        [
                            f"Для {duplicate['service_name']} уже есть активная запись.",
                            "Заменить старую или сохранить обе?",
                        ]
                    ),
                    reply_markup=duplicate_keyboard(),
                )
            return

        await callback.answer("Сохраняю")
        if callback.message:
            await finalize_trial_save(callback.message, user, draft)

    @router.callback_query(F.data.startswith("dup:"))
    async def duplicate_handler(callback: CallbackQuery) -> None:
        user = await ensure_callback_user(callback)
        if not user:
            await callback.answer("Не нашел пользователя", show_alert=True)
            return

        session = await database.get_session(user["telegram_user_id"])
        if not session or session["state"] != "awaiting_duplicate_resolution":
            await callback.answer("Эта запись уже устарела", show_alert=True)
            return

        action = callback.data.split(":", maxsplit=1)[1]
        draft = TrialDraft.from_dict(session["payload"]["draft"])
        duplicate_trial_id = session["payload"]["duplicate_trial_id"]

        if action == "cancel":
            await database.clear_session(user["telegram_user_id"])
            await callback.answer("Ок")
            return

        if action == "replace":
            await database.archive_trial(duplicate_trial_id, now_utc_iso())
        elif action != "create":
            await callback.answer("Неизвестное действие", show_alert=True)
            return

        await callback.answer("Сохраняю")
        if callback.message:
            await finalize_trial_save(callback.message, user, draft)

    @router.callback_query(F.data.startswith("trial:"))
    async def trial_action_handler(callback: CallbackQuery) -> None:
        user = await ensure_callback_user(callback)
        if not user:
            await callback.answer("Не нашел пользователя", show_alert=True)
            return

        _, action, trial_id_text = callback.data.split(":")
        trial_id = int(trial_id_text)
        trial = await database.get_trial(trial_id)
        if not trial or trial["user_id"] != user["id"]:
            await callback.answer("Эта запись тебе недоступна", show_alert=True)
            return

        if trial["status"] not in {"active", "reminder_sent"}:
            await callback.answer("Статус уже обновлен", show_alert=True)
            return

        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        now_iso = now_dt.isoformat()

        if action == "cancel":
            if datetime.fromisoformat(trial["billing_at"]) < now_dt:
                await database.mark_trial_status(trial_id, "expired_no_response", now_iso)
                await callback.answer("Срок уже прошел", show_alert=True)
                return

            await database.cancel_pending_jobs(trial_id)
            await database.mark_trial_status(trial_id, "canceled_confirmed", now_iso)
            await database.record_event(
                user_id=user["id"],
                trial_id=trial_id,
                event_name="trial_canceled_confirmed",
                payload={"service_name": trial["service_name"]},
                now_iso=now_iso,
            )
            await callback.answer("Отметил")
            if callback.message:
                await strip_inline_keyboard(callback)
                if trial["amount_minor"] is not None and trial["currency_code"]:
                    text = (
                        "Готово. Отметил как отмененный и добавил в сэкономленное: "
                        f"{format_money(trial['amount_minor'], trial['currency_code'])}."
                    )
                else:
                    text = "Готово. Отметил trial как отмененный."
                await callback.message.answer(text)
            return

        if action == "keep":
            await database.cancel_pending_jobs(trial_id)
            await database.mark_trial_status(trial_id, "kept_by_user", now_iso)
            await database.record_event(
                user_id=user["id"],
                trial_id=trial_id,
                event_name="trial_kept",
                payload={"service_name": trial["service_name"]},
                now_iso=now_iso,
            )
            await callback.answer("Ок")
            if callback.message:
                await strip_inline_keyboard(callback)
                await callback.message.answer("Принял. По этой записи больше не напоминаю.")
            return

        if action == "snooze":
            if trial["snooze_count"] >= 1:
                await callback.answer("По этой записи уже был дополнительный пинг", show_alert=True)
                return

            remaining = datetime.fromisoformat(trial["billing_at"]) - now_dt
            if remaining <= timedelta(hours=1):
                await callback.answer("До конца trial уже слишком близко. Лучше проверить прямо сейчас.", show_alert=True)
                return

            next_reminder = min(
                now_dt + timedelta(hours=6),
                datetime.fromisoformat(trial["billing_at"]) - timedelta(hours=1),
            ).replace(microsecond=0)
            await database.increment_snooze_count(trial_id)
            await database.schedule_reminder(
                trial_id=trial_id,
                job_type="snooze",
                scheduled_at=next_reminder.isoformat(),
                now_iso=now_iso,
            )
            await database.record_event(
                user_id=user["id"],
                trial_id=trial_id,
                event_name="trial_snoozed",
                payload={"next_reminder": next_reminder.isoformat()},
                now_iso=now_iso,
            )
            await callback.answer("Ок")
            if callback.message:
                await strip_inline_keyboard(callback)
                await callback.message.answer("Хорошо. Напомню позже еще один раз.")
            return

        await callback.answer("Неизвестное действие", show_alert=True)

    @router.message(F.text)
    async def text_handler(message: Message) -> None:
        user = await ensure_user(message)
        session = await database.get_session(user["telegram_user_id"])
        text = (message.text or "").strip()

        draft = parse_trial_text(text, timezone_name=settings.app_timezone)
        if draft:
            await database.record_event(
                user_id=user["id"],
                trial_id=None,
                event_name="trial_parse_succeeded",
                payload={"service_name": draft.service_name},
                now_iso=now_utc_iso(),
            )
            await continue_draft_flow(message, user, draft)
            return

        if session and session["state"] == "awaiting_date":
            await handle_date_followup(message, user, session)
            return

        await database.record_event(
            user_id=user["id"],
            trial_id=None,
            event_name="trial_parse_failed",
            payload={"text": text},
            now_iso=now_utc_iso(),
        )
        await message.answer(build_parse_failed_text())

    async def handle_date_followup(message: Message, user: dict, session: dict) -> None:
        billing_at = parse_date_only(
            message.text or "",
            timezone_name=settings.app_timezone,
        )
        if not billing_at:
            await message.answer(
                "\n".join(
                    [
                        "Все еще не понял срок.",
                        "Попробуй так: 14 дней, 2 недели, полмесяца, 5 мая",
                    ]
                )
            )
            return

        draft = TrialDraft.from_dict(session["payload"]["draft"])
        draft.billing_at = billing_at
        await continue_draft_flow(message, user, draft)

    dispatcher.include_router(router)
    return dispatcher


async def reminder_worker(bot: Bot, database: Database, settings: Settings) -> None:
    while True:
        try:
            now_iso = now_utc_iso()
            await database.expire_overdue_trials(now_iso)
            due_jobs = await database.claim_due_jobs(now_iso, settings.reminder_batch_size)
            for job in due_jobs:
                await process_reminder_job(bot, database, job, timezone_name=settings.app_timezone)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reminder worker tick failed")
        await asyncio.sleep(settings.reminder_poll_seconds)


async def process_reminder_job(bot: Bot, database: Database, job: dict, timezone_name: str) -> None:
    now_dt = datetime.now(timezone.utc).replace(microsecond=0)
    now_iso = now_dt.isoformat()
    try:
        sent_message = await bot.send_message(
            chat_id=job["telegram_user_id"],
            text=build_reminder_text(job, timezone_name=timezone_name),
            reply_markup=reminder_keyboard(job["trial_id"]),
        )
        await database.mark_job_sent(job["id"], sent_message.message_id, now_iso)
        await database.mark_trial_status(job["trial_id"], "reminder_sent", now_iso)
        await database.record_event(
            user_id=job["user_id"],
            trial_id=job["trial_id"],
            event_name="reminder_sent",
            payload={"job_type": job["job_type"]},
            now_iso=now_iso,
        )
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("Permanent reminder failure for job %s: %s", job["id"], exc)
        await database.mark_job_failed(job["id"], str(exc))
        await database.record_event(
            user_id=job["user_id"],
            trial_id=job["trial_id"],
            event_name="reminder_failed",
            payload={"error": str(exc), "permanent": True},
            now_iso=now_iso,
        )
    except Exception as exc:
        logger.exception("Temporary reminder failure for job %s", job["id"])
        if job["retry_count"] >= 2:
            await database.mark_job_failed(job["id"], str(exc))
        else:
            retry_at = (now_dt + timedelta(minutes=5)).isoformat()
            await database.reschedule_job(job["id"], retry_at, str(exc))
        await database.record_event(
            user_id=job["user_id"],
            trial_id=job["trial_id"],
            event_name="reminder_failed",
            payload={"error": str(exc), "permanent": False},
            now_iso=now_iso,
        )


def calculate_primary_reminder(billing_at_iso: str, now_iso: str, timezone_name: str) -> str:
    tz = ZoneInfo(timezone_name)
    billing_local = datetime.fromisoformat(billing_at_iso).astimezone(tz)
    now_local = datetime.fromisoformat(now_iso).astimezone(tz)

    reminder_local = (billing_local - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    if reminder_local > now_local:
        return reminder_local.astimezone(timezone.utc).isoformat()

    if billing_local - now_local > timedelta(hours=6):
        fallback_local = (now_local + timedelta(hours=3)).replace(second=0, microsecond=0)
        return fallback_local.astimezone(timezone.utc).isoformat()

    return now_local.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def build_preview_text(draft: TrialDraft, timezone_name: str) -> str:
    lines = [
        "Проверь запись:",
        f"Сервис: {draft.service_name}",
        f"До конца trial: {format_billing_date(draft.billing_at, timezone_name)}" if draft.billing_at else "До конца trial: не указано",
    ]
    if draft.amount_minor is not None and draft.currency_code:
        lines.append(f"Стоимость, если не отменить: {format_money(draft.amount_minor, draft.currency_code)}")
    else:
        lines.append("Стоимость: не указана")
    return "\n".join(lines)


def build_reminder_text(job: dict, timezone_name: str) -> str:
    deadline_label = describe_deadline(job["billing_at"], timezone_name)
    lines = [f"{deadline_label} заканчивается trial {job['service_name']}."]
    if job["amount_minor"] is not None and job["currency_code"]:
        lines.append(f"Если ничего не делать, может списаться {format_money(job['amount_minor'], job['currency_code'])}.")
    else:
        lines.append("Если продление не нужно, самое время отменить.")
    lines.append("Если уже отменил, просто нажми кнопку ниже.")
    return "\n".join(lines)


def describe_deadline(billing_at_iso: str, timezone_name: str) -> str:
    tz = ZoneInfo(timezone_name)
    billing_local = datetime.fromisoformat(billing_at_iso).astimezone(tz).date()
    today_local = datetime.now(tz).date()
    days_diff = (billing_local - today_local).days
    if days_diff <= 0:
        return "Сегодня"
    if days_diff == 1:
        return "Завтра"
    return f"{billing_local.strftime('%d.%m.%Y')}"


def build_input_help_text() -> str:
    return "\n".join(
        [
            "Сохраняй trial одним сообщением.",
            "",
            "Пример:",
            "ChatGPT | 14 дней | 20 долларов",
            "",
            "Формат:",
            "название сервиса | срок trial или дата конца | стоимость подписки (необязательно)",
            "",
            "Тоже пойму:",
            "Claude 2 недели",
            "Cursor на месяц",
            "Cloud 3 months 10 usd",
        ]
    )


def build_parse_failed_text() -> str:
    return "\n".join(
        [
            "Не смог разобрать запись.",
            "",
            "Попробуй так:",
            "ChatGPT | 14 дней | 20 долларов",
            "",
            "Или проще:",
            "Claude 2 недели",
        ]
    )


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


async def strip_inline_keyboard(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        return
