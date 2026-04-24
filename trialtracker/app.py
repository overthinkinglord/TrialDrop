from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from trialtracker.config import Settings
from trialtracker.database import Database
from trialtracker.formatting import format_local_datetime, format_money, format_saved_totals, format_trial_line
from trialtracker.keyboards import draft_preview_keyboard, duplicate_keyboard, reminder_keyboard, timezone_keyboard
from trialtracker.models import TrialDraft
from trialtracker.parser import parse_amount_only, parse_date_only, parse_trial_text


logger = logging.getLogger(__name__)

TIMEZONE_ALIASES = {
    "berlin": "Europe/Berlin",
    "germany": "Europe/Berlin",
    "london": "Europe/London",
    "new york": "America/New_York",
    "nyc": "America/New_York",
    "tbilisi": "Asia/Tbilisi",
    "utc": "UTC",
}


def build_dispatcher(database: Database, settings: Settings) -> Dispatcher:
    dispatcher = Dispatcher()
    router = Router()

    async def ensure_user(message: Message) -> dict:
        now_iso = now_utc_iso()
        user = await database.upsert_user(message.from_user, now_iso)
        return user

    async def ensure_callback_user(callback: CallbackQuery) -> Optional[dict]:
        if callback.from_user is None:
            return None
        return await database.fetch_user_by_telegram_id(callback.from_user.id)

    async def send_timezone_prompt(target: Message, prefix: Optional[str] = None) -> None:
        lines = []
        if prefix:
            lines.append(prefix)
            lines.append("")
        lines.append("Сначала зафиксируем твой timezone.")
        lines.append("Выбери кнопку ниже или отправь IANA timezone, например: Europe/Berlin")
        await target.answer("\n".join(lines), reply_markup=timezone_keyboard())

    async def show_ready_prompt(target: Message, timezone_name: str) -> None:
        await target.answer(
            "\n".join(
                [
                    f"Timezone сохранен: {timezone_name}",
                    "",
                    "Теперь просто отправь trial в таком виде:",
                    "ChatGPT 14 дней $20",
                ]
            )
        )

    async def show_draft_preview(target: Message, user: dict, draft: TrialDraft) -> None:
        await database.set_session(
            telegram_user_id=user["telegram_user_id"],
            state="awaiting_save_confirmation",
            payload={"draft": draft.to_dict()},
            now_iso=now_utc_iso(),
        )
        await target.answer(
            build_preview_text(draft=draft, timezone_name=user["timezone"]),
            reply_markup=draft_preview_keyboard(),
        )

    async def continue_draft_flow(target: Message, user: dict, draft: TrialDraft) -> None:
        missing = draft.missing_fields()
        if "amount" in missing:
            await database.set_session(
                telegram_user_id=user["telegram_user_id"],
                state="awaiting_amount",
                payload={"draft": draft.to_dict()},
                now_iso=now_utc_iso(),
            )
            await target.answer("Сколько спишут, если не отменить? Например: $20")
            return

        if "date" in missing:
            await database.set_session(
                telegram_user_id=user["telegram_user_id"],
                state="awaiting_date",
                payload={"draft": draft.to_dict()},
                now_iso=now_utc_iso(),
            )
            await target.answer("Когда будет списание или сколько длится trial? Например: 14 дней")
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
                f"У тебя уже есть активный trial для {duplicate['service_name']}. Что сделать?",
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
        reminder_at = calculate_primary_reminder(draft.billing_at, now_iso)
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
        await target.answer(
            "\n".join(
                [
                    "Готово. Trial сохранен.",
                    f"Напомню {format_local_datetime(reminder_at, user['timezone'])} по твоему времени.",
                ]
            )
        )

    async def resume_after_timezone(target: Message, user: dict, session: Optional[dict]) -> None:
        if not session:
            await show_ready_prompt(target, user["timezone"])
            return

        pending_text = session["payload"].get("pending_text")
        if not pending_text:
            await database.clear_session(user["telegram_user_id"])
            await show_ready_prompt(target, user["timezone"])
            return

        await database.clear_session(user["telegram_user_id"])
        draft = parse_trial_text(pending_text, timezone_name=user["timezone"])
        if draft is None:
            await target.answer("Timezone сохранил, но прошлое сообщение не смог распарсить. Пришли его еще раз.")
            return
        await continue_draft_flow(target, user, draft)

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

        if not user.get("timezone"):
            await database.set_session(
                telegram_user_id=user["telegram_user_id"],
                state="awaiting_timezone",
                payload={},
                now_iso=now_utc_iso(),
            )
            await message.answer(
                "\n".join(
                    [
                        "Я помогаю не забывать отменять trial-подписки.",
                        "",
                        "Напиши так: ChatGPT 14 дней $20",
                        "Я напомню до списания и покажу, сколько денег ты спас.",
                    ]
                )
            )
            await send_timezone_prompt(message)
            return

        await message.answer(
            "\n".join(
                [
                    "Напиши trial одной строкой.",
                    "Пример: ChatGPT 14 дней $20",
                    "",
                    "Команды: /list /stats /timezone /help",
                ]
            )
        )

    @router.message(Command("help"))
    async def help_handler(message: Message) -> None:
        await ensure_user(message)
        await message.answer(
            "\n".join(
                [
                    "Как пользоваться:",
                    "1. Отправь: ChatGPT 14 дней $20",
                    "2. Подтверди сохранение",
                    "3. Получи reminder до списания",
                    "",
                    "Команды:",
                    "/add — пример формата",
                    "/list — активные trial",
                    "/stats — saved money",
                    "/timezone — сменить timezone",
                ]
            )
        )

    @router.message(Command("add"))
    async def add_handler(message: Message) -> None:
        await ensure_user(message)
        await message.answer(
            "\n".join(
                [
                    "Отправь trial одной строкой.",
                    "Примеры:",
                    "ChatGPT 14 дней $20",
                    "Claude 7 days $20",
                    "Perplexity до 5 мая €20",
                ]
            )
        )

    @router.message(Command("timezone"))
    async def timezone_handler(message: Message) -> None:
        user = await ensure_user(message)
        argument = extract_command_argument(message.text or "")
        if not argument:
            await database.set_session(
                telegram_user_id=user["telegram_user_id"],
                state="awaiting_timezone",
                payload={},
                now_iso=now_utc_iso(),
            )
            await send_timezone_prompt(message)
            return

        timezone_name = resolve_timezone(argument)
        if timezone_name is None:
            await message.answer("Не узнал timezone. Пример: /timezone Europe/Berlin")
            return

        await database.set_timezone(user["telegram_user_id"], timezone_name)
        await database.record_event(
            user_id=user["id"],
            trial_id=None,
            event_name="timezone_set",
            payload={"timezone": timezone_name},
            now_iso=now_utc_iso(),
        )
        await message.answer(f"Timezone обновлен: {timezone_name}")

    @router.message(Command("list"))
    async def list_handler(message: Message) -> None:
        user = await ensure_user(message)
        await database.expire_overdue_trials(now_utc_iso())
        trials = await database.list_active_trials(user["id"])
        if not trials:
            await message.answer("Активных trial сейчас нет.")
            return

        lines = ["Активные trial:"]
        for trial in trials:
            lines.append(f"• {format_trial_line(trial, user['timezone'])}")
        await message.answer("\n".join(lines))

    @router.message(Command("stats"))
    async def stats_handler(message: Message) -> None:
        user = await ensure_user(message)
        await database.expire_overdue_trials(now_utc_iso())
        totals = await database.get_saved_totals(user["id"])
        active_count = await database.count_active_trials(user["id"])
        next_trial = await database.get_next_upcoming_trial(user["id"])
        await database.record_event(
            user_id=user["id"],
            trial_id=None,
            event_name="stats_viewed",
            payload={"active_count": active_count},
            now_iso=now_utc_iso(),
        )

        lines = [
            f"Подтвержденно спасено: {format_saved_totals(totals)}",
            f"Активных trial: {active_count}",
        ]
        if next_trial:
            lines.append(
                "Ближайшее списание: "
                f"{next_trial['service_name']}, "
                f"{format_local_datetime(next_trial['billing_at'], user['timezone'])}, "
                f"{format_money(next_trial['amount_minor'], next_trial['currency_code'])}"
            )
        await message.answer("\n".join(lines))

    @router.callback_query(F.data.startswith("tz:"))
    async def timezone_callback_handler(callback: CallbackQuery) -> None:
        user = await ensure_callback_user(callback)
        if not user:
            await callback.answer("Не нашел пользователя", show_alert=True)
            return

        timezone_name = callback.data.split(":", maxsplit=1)[1]
        if resolve_timezone(timezone_name) is None:
            await callback.answer("Некорректный timezone", show_alert=True)
            return

        await database.set_timezone(user["telegram_user_id"], timezone_name)
        await database.record_event(
            user_id=user["id"],
            trial_id=None,
            event_name="timezone_set",
            payload={"timezone": timezone_name},
            now_iso=now_utc_iso(),
        )
        session = await database.get_session(user["telegram_user_id"])
        await callback.answer("Timezone сохранен")
        if callback.message:
            await callback.message.answer(f"Timezone сохранен: {timezone_name}")
            await resume_after_timezone(callback.message, {**user, "timezone": timezone_name}, session)

    @router.callback_query(F.data == "draft:edit")
    async def draft_edit_handler(callback: CallbackQuery) -> None:
        user = await ensure_callback_user(callback)
        if not user:
            await callback.answer("Не нашел пользователя", show_alert=True)
            return
        await database.clear_session(user["telegram_user_id"])
        await callback.answer("Пришли trial заново")
        if callback.message:
            await callback.message.answer("Ок. Отправь trial еще раз одной строкой.")

    @router.callback_query(F.data == "draft:save")
    async def draft_save_handler(callback: CallbackQuery) -> None:
        user = await ensure_callback_user(callback)
        if not user:
            await callback.answer("Не нашел пользователя", show_alert=True)
            return
        session = await database.get_session(user["telegram_user_id"])
        if not session or session["state"] != "awaiting_save_confirmation":
            await callback.answer("Черновик уже устарел", show_alert=True)
            return

        draft = TrialDraft.from_dict(session["payload"]["draft"])
        if not draft.is_complete:
            await callback.answer("Черновик неполный", show_alert=True)
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
                    f"У тебя уже есть активный trial для {duplicate['service_name']}. Что сделать?",
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
            await callback.answer("Черновик уже устарел", show_alert=True)
            return

        action = callback.data.split(":", maxsplit=1)[1]
        draft = TrialDraft.from_dict(session["payload"]["draft"])
        duplicate_trial_id = session["payload"]["duplicate_trial_id"]

        if action == "cancel":
            await database.clear_session(user["telegram_user_id"])
            await callback.answer("Ок, ничего не сохраняю")
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
            await callback.answer("Этот trial недоступен", show_alert=True)
            return

        if trial["status"] not in {"active", "reminder_sent"}:
            await callback.answer("Статус уже обновлен", show_alert=True)
            return

        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        now_iso = now_dt.isoformat()

        if action == "cancel":
            if datetime.fromisoformat(trial["billing_at"]) < now_dt:
                await database.mark_trial_status(trial_id, "expired_no_response", now_iso)
                await callback.answer("Дата уже прошла. В saved money не засчитываю.", show_alert=True)
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
            await callback.answer("Отлично")
            if callback.message:
                await strip_inline_keyboard(callback)
                await callback.message.answer(
                    f"Отлично. Засчитал как спасенные деньги: "
                    f"{format_money(trial['amount_minor'], trial['currency_code'])}."
                )
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
                await callback.message.answer("Ок, не напоминаю. Этот trial не пойдет в saved money.")
            return

        if action == "snooze":
            if trial["snooze_count"] >= 1:
                await callback.answer("Для этого trial snooze уже использован", show_alert=True)
                return

            remaining = datetime.fromisoformat(trial["billing_at"]) - now_dt
            if remaining <= timedelta(minutes=45):
                await callback.answer("До списания уже слишком близко. Лучше отменить сейчас.", show_alert=True)
                return

            next_reminder = min(
                now_dt + timedelta(hours=3),
                datetime.fromisoformat(trial["billing_at"]) - timedelta(minutes=15),
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
                await callback.message.answer("Хорошо. Напомню еще раз через 3 часа.")
            return

        await callback.answer("Неизвестное действие", show_alert=True)

    @router.message(F.text)
    async def text_handler(message: Message) -> None:
        user = await ensure_user(message)
        session = await database.get_session(user["telegram_user_id"])
        text = (message.text or "").strip()

        timezone_name = resolve_timezone(text)
        if timezone_name and session and session["state"] == "awaiting_timezone":
            await database.set_timezone(user["telegram_user_id"], timezone_name)
            await database.record_event(
                user_id=user["id"],
                trial_id=None,
                event_name="timezone_set",
                payload={"timezone": timezone_name},
                now_iso=now_utc_iso(),
            )
            user["timezone"] = timezone_name
            await resume_after_timezone(message, user, session)
            return

        parse_timezone = user.get("timezone") or "UTC"
        draft = parse_trial_text(text, timezone_name=parse_timezone)
        if draft:
            if not user.get("timezone"):
                await database.set_session(
                    telegram_user_id=user["telegram_user_id"],
                    state="awaiting_timezone",
                    payload={"pending_text": text},
                    now_iso=now_utc_iso(),
                )
                await send_timezone_prompt(
                    message,
                    prefix="Сначала нужен timezone, чтобы reminder пришел в правильное время.",
                )
                return

            await database.record_event(
                user_id=user["id"],
                trial_id=None,
                event_name="trial_parse_succeeded",
                payload={"service_name": draft.service_name},
                now_iso=now_utc_iso(),
            )
            await continue_draft_flow(message, user, draft)
            return

        if session and session["state"] == "awaiting_amount":
            await handle_amount_followup(message, user, session)
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
        await message.answer("Не до конца понял формат. Отправь так: ChatGPT 14 дней $20")

    async def handle_amount_followup(message: Message, user: dict, session: dict) -> None:
        amount_result = parse_amount_only(message.text or "")
        if not amount_result:
            await message.answer("Не увидел сумму. Пример: $20")
            return

        draft = TrialDraft.from_dict(session["payload"]["draft"])
        draft.amount_minor, draft.currency_code = amount_result
        await continue_draft_flow(message, user, draft)

    async def handle_date_followup(message: Message, user: dict, session: dict) -> None:
        billing_at = parse_date_only(
            message.text or "",
            timezone_name=user["timezone"],
        )
        if not billing_at:
            await message.answer("Не понял дату. Пример: 14 дней или 5 мая")
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
                await process_reminder_job(bot, database, job)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reminder worker tick failed")
        await asyncio.sleep(settings.reminder_poll_seconds)


async def process_reminder_job(bot: Bot, database: Database, job: dict) -> None:
    now_dt = datetime.now(timezone.utc).replace(microsecond=0)
    now_iso = now_dt.isoformat()
    try:
        sent_message = await bot.send_message(
            chat_id=job["telegram_user_id"],
            text=build_reminder_text(job, timezone_name=job.get("timezone") or "UTC"),
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


def calculate_primary_reminder(billing_at_iso: str, now_iso: str) -> str:
    billing_at = datetime.fromisoformat(billing_at_iso)
    now_dt = datetime.fromisoformat(now_iso)
    delta = billing_at - now_dt
    if delta > timedelta(hours=24):
        reminder_at = billing_at - timedelta(hours=24)
    elif delta > timedelta(hours=3):
        reminder_at = billing_at - timedelta(hours=3)
    else:
        reminder_at = now_dt
    return reminder_at.replace(microsecond=0).isoformat()


def build_preview_text(draft: TrialDraft, timezone_name: str) -> str:
    billing_text = format_local_datetime(draft.billing_at, timezone_name) if draft.billing_at else "не указано"
    amount_text = (
        format_money(draft.amount_minor, draft.currency_code)
        if draft.amount_minor is not None and draft.currency_code
        else "не указано"
    )
    return "\n".join(
        [
            "Понял так:",
            f"Сервис: {draft.service_name}",
            f"Списание: {billing_text}",
            f"Сумма: {amount_text}",
        ]
    )


def build_reminder_text(job: dict, timezone_name: str) -> str:
    return (
        f"Скоро спишут {format_money(job['amount_minor'], job['currency_code'])} "
        f"за {job['service_name']}.\n"
        f"Ожидаемое списание: {format_local_datetime(job['billing_at'], timezone_name)}\n"
        "Если уже отменил, нажми кнопку ниже."
    )


def resolve_timezone(value: str) -> Optional[str]:
    candidate = value.strip()
    if not candidate:
        return None
    candidate = TIMEZONE_ALIASES.get(candidate.lower(), candidate)
    try:
        ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        return None
    return candidate


def extract_command_argument(text: str) -> str:
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


async def strip_inline_keyboard(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        return
