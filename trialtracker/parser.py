from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Optional
from zoneinfo import ZoneInfo

from trialtracker.models import TrialDraft


CURRENCY_SYMBOLS = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "₽": "RUB",
}

CURRENCY_ALIASES = {
    "usd": "USD",
    "eur": "EUR",
    "gbp": "GBP",
    "rub": "RUB",
    "rur": "RUB",
}

MONTH_ALIASES = {
    "jan": 1,
    "january": 1,
    "янв": 1,
    "января": 1,
    "feb": 2,
    "february": 2,
    "фев": 2,
    "февраля": 2,
    "mar": 3,
    "march": 3,
    "мар": 3,
    "марта": 3,
    "apr": 4,
    "april": 4,
    "апр": 4,
    "апреля": 4,
    "may": 5,
    "мая": 5,
    "jun": 6,
    "june": 6,
    "июн": 6,
    "июня": 6,
    "jul": 7,
    "july": 7,
    "июл": 7,
    "июля": 7,
    "aug": 8,
    "august": 8,
    "авг": 8,
    "августа": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "сен": 9,
    "сентября": 9,
    "oct": 10,
    "october": 10,
    "окт": 10,
    "октября": 10,
    "nov": 11,
    "november": 11,
    "ноя": 11,
    "ноября": 11,
    "dec": 12,
    "december": 12,
    "дек": 12,
    "декабря": 12,
}

SERVICE_ALIASES = {
    "gpt": "chatgpt",
    "openai chatgpt": "chatgpt",
}

CANONICAL_SERVICE_NAMES = {
    "chatgpt": "ChatGPT",
    "claude": "Claude",
    "cursor": "Cursor",
    "perplexity": "Perplexity",
    "midjourney": "Midjourney",
}

FILLER_PATTERNS = [
    r"\brenews?\b",
    r"\bon\b",
    r"\btrial\b",
    r"\bдо\b",
    r"\bчерез\b",
]


@dataclass
class AmountMatch:
    amount_minor: int
    currency_code: str
    remaining_text: str


@dataclass
class DateMatch:
    billing_at_utc: datetime
    remaining_text: str


def normalize_service_key(service_name: str) -> str:
    key = re.sub(r"[^a-z0-9а-я]+", " ", service_name.lower()).strip()
    key = re.sub(r"\s+", " ", key)
    return SERVICE_ALIASES.get(key, key)


def canonical_service_name(service_name: str) -> str:
    key = normalize_service_key(service_name)
    return CANONICAL_SERVICE_NAMES.get(key, service_name.strip())


def parse_trial_text(
    text: str,
    timezone_name: str,
    now: Optional[datetime] = None,
) -> Optional[TrialDraft]:
    clean_text = " ".join(text.strip().split())
    if not clean_text:
        return None

    now_utc = now or datetime.now(timezone.utc)
    tz = ZoneInfo(timezone_name)
    amount_match = parse_amount_fragment(clean_text)
    text_without_amount = amount_match.remaining_text if amount_match else clean_text
    date_match = parse_date_fragment(text_without_amount, timezone_name=timezone_name, now=now_utc)
    text_without_parts = date_match.remaining_text if date_match else text_without_amount

    service_name = cleanup_service_name(text_without_parts)
    if not service_name:
        return None

    if amount_match is None and date_match is None:
        return None

    started_at = now_utc.astimezone(tz).astimezone(timezone.utc).replace(microsecond=0)

    return TrialDraft(
        service_name=canonical_service_name(service_name),
        service_key_normalized=normalize_service_key(service_name),
        amount_minor=amount_match.amount_minor if amount_match else None,
        currency_code=amount_match.currency_code if amount_match else None,
        started_at=started_at.isoformat(),
        billing_at=date_match.billing_at_utc.replace(microsecond=0).isoformat() if date_match else None,
        raw_input=clean_text,
    )


def parse_amount_only(text: str) -> Optional[tuple[int, str]]:
    amount_match = parse_amount_fragment(text)
    if not amount_match:
        return None
    return amount_match.amount_minor, amount_match.currency_code


def parse_date_only(text: str, timezone_name: str, now: Optional[datetime] = None) -> Optional[str]:
    date_match = parse_date_fragment(text, timezone_name=timezone_name, now=now)
    if not date_match:
        return None
    return date_match.billing_at_utc.replace(microsecond=0).isoformat()


def parse_amount_fragment(text: str) -> Optional[AmountMatch]:
    patterns = [
        re.compile(r"(?P<currency>[$€£₽])\s*(?P<amount>\d+(?:[.,]\d{1,2})?)", re.IGNORECASE),
        re.compile(
            r"(?P<amount>\d+(?:[.,]\d{1,2})?)\s*(?P<currency>usd|eur|gbp|rub|rur)",
            re.IGNORECASE,
        ),
    ]

    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue

        amount_text = match.group("amount").replace(",", ".")
        currency_token = match.group("currency").lower()
        currency_code = CURRENCY_SYMBOLS.get(currency_token, CURRENCY_ALIASES.get(currency_token))
        if not currency_code:
            continue

        try:
            amount_minor = int((Decimal(amount_text) * 100).quantize(Decimal("1")))
        except InvalidOperation:
            return None

        remaining_text = (text[: match.start()] + " " + text[match.end() :]).strip()
        return AmountMatch(
            amount_minor=amount_minor,
            currency_code=currency_code,
            remaining_text=" ".join(remaining_text.split()),
        )

    return None


def parse_date_fragment(
    text: str,
    timezone_name: str,
    now: Optional[datetime] = None,
) -> Optional[DateMatch]:
    now_utc = now or datetime.now(timezone.utc)
    local_now = now_utc.astimezone(ZoneInfo(timezone_name))

    relative_match = re.search(r"(?P<days>\d{1,3})\s*(days?|d|день|дня|дней)\b", text, re.IGNORECASE)
    if relative_match:
        days = int(relative_match.group("days"))
        billing_local = (local_now + timedelta(days=days)).replace(microsecond=0)
        remaining_text = (text[: relative_match.start()] + " " + text[relative_match.end() :]).strip()
        return DateMatch(
            billing_at_utc=billing_local.astimezone(timezone.utc),
            remaining_text=" ".join(remaining_text.split()),
        )

    iso_match = re.search(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b", text)
    if iso_match:
        billing_local = build_local_datetime(
            now_local=local_now,
            day=int(iso_match.group("day")),
            month=int(iso_match.group("month")),
            year=int(iso_match.group("year")),
        )
        if billing_local:
            remaining_text = (text[: iso_match.start()] + " " + text[iso_match.end() :]).strip()
            return DateMatch(
                billing_at_utc=billing_local.astimezone(timezone.utc),
                remaining_text=" ".join(remaining_text.split()),
            )

    numeric_match = re.search(
        r"\b(?P<day>\d{1,2})[./-](?P<month>\d{1,2})(?:[./-](?P<year>\d{2,4}))?\b",
        text,
    )
    if numeric_match:
        year = numeric_match.group("year")
        billing_local = build_local_datetime(
            now_local=local_now,
            day=int(numeric_match.group("day")),
            month=int(numeric_match.group("month")),
            year=int(expand_year(year)) if year else None,
        )
        if billing_local:
            remaining_text = (text[: numeric_match.start()] + " " + text[numeric_match.end() :]).strip()
            return DateMatch(
                billing_at_utc=billing_local.astimezone(timezone.utc),
                remaining_text=" ".join(remaining_text.split()),
            )

    day_month_match = re.search(
        r"\b(?P<day>\d{1,2})\s+(?P<month_name>[A-Za-zА-Яа-я]+)(?:\s+(?P<year>\d{4}))?\b",
        text,
        re.IGNORECASE,
    )
    if day_month_match:
        month = month_from_name(day_month_match.group("month_name"))
        if month:
            billing_local = build_local_datetime(
                now_local=local_now,
                day=int(day_month_match.group("day")),
                month=month,
                year=int(day_month_match.group("year")) if day_month_match.group("year") else None,
            )
            if billing_local:
                remaining_text = (
                    text[: day_month_match.start()] + " " + text[day_month_match.end() :]
                ).strip()
                return DateMatch(
                    billing_at_utc=billing_local.astimezone(timezone.utc),
                    remaining_text=" ".join(remaining_text.split()),
                )

    month_day_match = re.search(
        r"\b(?P<month_name>[A-Za-z]+)\s+(?P<day>\d{1,2})(?:,\s*(?P<year>\d{4}))?\b",
        text,
        re.IGNORECASE,
    )
    if month_day_match:
        month = month_from_name(month_day_match.group("month_name"))
        if month:
            billing_local = build_local_datetime(
                now_local=local_now,
                day=int(month_day_match.group("day")),
                month=month,
                year=int(month_day_match.group("year")) if month_day_match.group("year") else None,
            )
            if billing_local:
                remaining_text = (
                    text[: month_day_match.start()] + " " + text[month_day_match.end() :]
                ).strip()
                return DateMatch(
                    billing_at_utc=billing_local.astimezone(timezone.utc),
                    remaining_text=" ".join(remaining_text.split()),
                )

    return None


def build_local_datetime(
    now_local: datetime,
    day: int,
    month: int,
    year: Optional[int],
) -> Optional[datetime]:
    target_year = year or now_local.year
    try:
        candidate = now_local.replace(year=target_year, month=month, day=day, second=0, microsecond=0)
    except ValueError:
        return None

    if year is None and candidate < now_local:
        try:
            candidate = candidate.replace(year=candidate.year + 1)
        except ValueError:
            return None

    return candidate


def expand_year(year_text: str) -> int:
    year = int(year_text)
    if year < 100:
        return 2000 + year
    return year


def month_from_name(value: str) -> Optional[int]:
    return MONTH_ALIASES.get(value.strip().lower())


def cleanup_service_name(text: str) -> str:
    cleaned = text
    for pattern in FILLER_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[,:;]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .-")

