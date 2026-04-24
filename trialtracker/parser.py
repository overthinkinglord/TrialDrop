from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
import re
from typing import Optional
from zoneinfo import ZoneInfo

from trialtracker.models import TrialDraft


CURRENCY_SYMBOLS = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "₽": "RUB",
    "₴": "UAH",
}

CURRENCY_WORD_GROUPS = {
    "USD": [
        "usd",
        "dollar",
        "dollars",
        "доллар",
        "доллара",
        "долларов",
        "бакс",
        "бакса",
        "баксов",
        "дол",
    ],
    "EUR": [
        "eur",
        "euro",
        "euros",
        "евро",
    ],
    "GBP": [
        "gbp",
        "pound",
        "pounds",
        "фунт",
        "фунта",
        "фунтов",
    ],
    "RUB": [
        "rub",
        "rur",
        "ruble",
        "rubles",
        "руб",
        "рубль",
        "рубля",
        "рублей",
    ],
    "UAH": [
        "uah",
        "hryvnia",
        "hryvnias",
        "грн",
        "гривна",
        "гривны",
        "гривен",
    ],
}

TOKEN_TO_CURRENCY = {
    token: code for code, tokens in CURRENCY_WORD_GROUPS.items() for token in tokens
}

CURRENCY_TOKEN_PATTERN = "|".join(
    sorted((re.escape(token) for token in TOKEN_TO_CURRENCY), key=len, reverse=True)
)

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

NUMBER_WORDS = {
    "one": 1,
    "a": 1,
    "an": 1,
    "один": 1,
    "одна": 1,
    "одну": 1,
    "single": 1,
    "two": 2,
    "два": 2,
    "две": 2,
    "three": 3,
    "три": 3,
    "four": 4,
    "четыре": 4,
    "five": 5,
    "пять": 5,
    "six": 6,
    "шесть": 6,
    "seven": 7,
    "семь": 7,
    "eight": 8,
    "восемь": 8,
    "nine": 9,
    "девять": 9,
    "ten": 10,
    "десять": 10,
    "eleven": 11,
    "одиннадцать": 11,
    "twelve": 12,
    "двенадцать": 12,
}

DURATION_UNITS = {
    "day": "days",
    "days": "days",
    "d": "days",
    "день": "days",
    "дня": "days",
    "дней": "days",
    "week": "weeks",
    "weeks": "weeks",
    "неделя": "weeks",
    "недели": "weeks",
    "недель": "weeks",
    "month": "months",
    "months": "months",
    "месяц": "months",
    "месяца": "months",
    "месяцев": "months",
    "year": "years",
    "years": "years",
    "год": "years",
    "года": "years",
    "лет": "years",
}

SPECIAL_DURATION_PATTERNS = [
    (re.compile(r"\b(?:полмесяца|пол\s+месяца|half\s+month)\b", re.IGNORECASE), ("days", 15)),
    (re.compile(r"\b(?:полгода|пол\s+года|half\s+year)\b", re.IGNORECASE), ("months", 6)),
    (re.compile(r"\b(?:месяц|month)\b", re.IGNORECASE), ("months", 1)),
    (re.compile(r"\b(?:год|year)\b", re.IGNORECASE), ("years", 1)),
    (re.compile(r"\b(?:неделя|week)\b", re.IGNORECASE), ("weeks", 1)),
]

RELATIVE_DAY_PATTERNS = {
    "сегодня": 0,
    "today": 0,
    "завтра": 1,
    "tomorrow": 1,
}

SERVICE_ALIASES = {
    "chat gpt": "chatgpt",
    "chatgpt": "chatgpt",
    "gpt": "chatgpt",
    "чат gpt": "chatgpt",
    "чат гпт": "chatgpt",
    "чатжпт": "chatgpt",
    "чат гптшка": "chatgpt",
    "сейчас gpt": "chatgpt",
    "сейчас гпт": "chatgpt",
    "claude": "claude",
    "claud": "claude",
    "cloud": "claude",
    "клауд": "claude",
    "cursor": "cursor",
    "курсор": "cursor",
    "perplexity": "perplexity",
    "перплексити": "perplexity",
    "midjourney": "midjourney",
    "миджорни": "midjourney",
}

CANONICAL_SERVICE_NAMES = {
    "chatgpt": "ChatGPT",
    "claude": "Claude",
    "cursor": "Cursor",
    "perplexity": "Perplexity",
    "midjourney": "Midjourney",
}

FILLER_PATTERNS = [
    r"\bsubscription\b",
    r"\bservice\b",
    r"\bподписка\b",
    r"\bсервис\b",
    r"\btrial\b",
    r"\brenews?\b",
    r"\brenews?\s+on\b",
    r"\bon\b",
    r"\bдо\b",
    r"\bчерез\b",
    r"\bна\b",
    r"\bfor\b",
    r"\buntil\b",
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


def parse_trial_text(
    text: str,
    timezone_name: str,
    now: Optional[datetime] = None,
) -> Optional[TrialDraft]:
    clean_text = " ".join(text.strip().split())
    if not clean_text:
        return None

    now_utc = now or datetime.now(timezone.utc)
    structured_parts = split_structured_fields(clean_text)
    if structured_parts:
        draft = parse_structured_trial(structured_parts, timezone_name=timezone_name, now=now_utc)
        if draft and (draft.billing_at or draft.amount_minor is not None):
            return draft

    amount_match = parse_amount_fragment(clean_text)
    text_without_amount = amount_match.remaining_text if amount_match else clean_text
    date_match = parse_date_fragment(text_without_amount, timezone_name=timezone_name, now=now_utc)
    text_without_parts = date_match.remaining_text if date_match else text_without_amount

    service_name = cleanup_service_name(text_without_parts)
    if not service_name:
        return None

    if amount_match is None and date_match is None:
        return None

    started_at = build_reference_local(now_utc, timezone_name).astimezone(timezone.utc)

    return TrialDraft(
        service_name=canonical_service_name(service_name),
        service_key_normalized=normalize_service_key(service_name),
        amount_minor=amount_match.amount_minor if amount_match else None,
        currency_code=amount_match.currency_code if amount_match else None,
        started_at=started_at.replace(microsecond=0).isoformat(),
        billing_at=date_match.billing_at_utc.replace(microsecond=0).isoformat() if date_match else None,
        raw_input=clean_text,
    )


def parse_structured_trial(
    parts: list[str],
    timezone_name: str,
    now: datetime,
) -> Optional[TrialDraft]:
    if len(parts) < 2:
        return None

    service_raw = cleanup_service_name(parts[0])
    if not service_raw:
        return None

    date_match = parse_date_fragment(parts[1], timezone_name=timezone_name, now=now)
    amount_match = parse_amount_fragment(parts[2]) if len(parts) >= 3 else None

    started_at = build_reference_local(now, timezone_name).astimezone(timezone.utc)
    return TrialDraft(
        service_name=canonical_service_name(service_raw),
        service_key_normalized=normalize_service_key(service_raw),
        amount_minor=amount_match.amount_minor if amount_match else None,
        currency_code=amount_match.currency_code if amount_match else None,
        started_at=started_at.replace(microsecond=0).isoformat(),
        billing_at=date_match.billing_at_utc.replace(microsecond=0).isoformat() if date_match else None,
        raw_input=" | ".join(parts),
    )


def split_structured_fields(text: str) -> Optional[list[str]]:
    if "|" not in text:
        return None
    parts = [part.strip() for part in text.split("|")]
    parts = [part for part in parts if part]
    return parts or None


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
        re.compile(r"(?P<currency>[$€£₽₴])\s*(?P<amount>\d+(?:[.,]\d{1,2})?)", re.IGNORECASE),
        re.compile(r"(?P<amount>\d+(?:[.,]\d{1,2})?)\s*(?P<currency>[$€£₽₴])", re.IGNORECASE),
        re.compile(
            rf"(?P<amount>\d+(?:[.,]\d{{1,2}})?)\s*(?P<currency>{CURRENCY_TOKEN_PATTERN})\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?P<currency>{CURRENCY_TOKEN_PATTERN})\s*(?P<amount>\d+(?:[.,]\d{{1,2}})?)",
            re.IGNORECASE,
        ),
    ]

    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue

        amount_text = match.group("amount").replace(",", ".")
        currency_token = match.group("currency").lower()
        currency_code = CURRENCY_SYMBOLS.get(currency_token) or TOKEN_TO_CURRENCY.get(currency_token)
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

    for keyword, offset in RELATIVE_DAY_PATTERNS.items():
        match = re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE)
        if match:
            billing_local = normalize_billing_local(local_now + timedelta(days=offset))
            remaining_text = (text[: match.start()] + " " + text[match.end() :]).strip()
            return DateMatch(
                billing_at_utc=billing_local.astimezone(timezone.utc),
                remaining_text=" ".join(remaining_text.split()),
            )

    duration_match = parse_duration_fragment(text, local_now)
    if duration_match:
        return duration_match

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


def parse_duration_fragment(text: str, local_now: datetime) -> Optional[DateMatch]:
    for pattern, (unit, count) in SPECIAL_DURATION_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        billing_local = add_relative_period(local_now, unit=unit, count=count)
        remaining_text = (text[: match.start()] + " " + text[match.end() :]).strip()
        return DateMatch(
            billing_at_utc=billing_local.astimezone(timezone.utc),
            remaining_text=" ".join(remaining_text.split()),
        )

    duration_pattern = re.compile(
        r"\b(?P<count>\d{1,3}|[A-Za-zА-Яа-я]+)\s+"
        r"(?P<unit>days?|day|d|день|дня|дней|weeks?|week|неделя|недели|недель|"
        r"months?|month|месяц|месяца|месяцев|years?|year|год|года|лет)\b",
        re.IGNORECASE,
    )
    match = duration_pattern.search(text)
    if not match:
        return None

    count = resolve_count(match.group("count"))
    unit = DURATION_UNITS.get(match.group("unit").lower())
    if count is None or unit is None:
        return None

    billing_local = add_relative_period(local_now, unit=unit, count=count)
    remaining_text = (text[: match.start()] + " " + text[match.end() :]).strip()
    return DateMatch(
        billing_at_utc=billing_local.astimezone(timezone.utc),
        remaining_text=" ".join(remaining_text.split()),
    )


def resolve_count(value: str) -> Optional[int]:
    if value.isdigit():
        return int(value)
    return NUMBER_WORDS.get(value.strip().lower())


def add_relative_period(local_now: datetime, unit: str, count: int) -> datetime:
    if unit == "days":
        return normalize_billing_local(local_now + timedelta(days=count))
    if unit == "weeks":
        return normalize_billing_local(local_now + timedelta(weeks=count))
    if unit == "months":
        return normalize_billing_local(add_months(local_now, count))
    if unit == "years":
        return normalize_billing_local(add_months(local_now, count * 12))
    raise ValueError(f"Unsupported duration unit: {unit}")


def add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def normalize_billing_local(value: datetime) -> datetime:
    return value.replace(hour=12, minute=0, second=0, microsecond=0)


def build_reference_local(now_utc: datetime, timezone_name: str) -> datetime:
    local_now = now_utc.astimezone(ZoneInfo(timezone_name))
    return normalize_billing_local(local_now)


def build_local_datetime(
    now_local: datetime,
    day: int,
    month: int,
    year: Optional[int],
) -> Optional[datetime]:
    target_year = year or now_local.year
    try:
        candidate = normalize_billing_local(
            now_local.replace(year=target_year, month=month, day=day)
        )
    except ValueError:
        return None

    if year is None and candidate.date() < now_local.date():
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


def normalize_service_key(service_name: str) -> str:
    key = re.sub(r"[^a-z0-9а-я]+", " ", service_name.lower()).strip()
    key = re.sub(r"\s+", " ", key)
    key = SERVICE_ALIASES.get(key, key)

    best_match = key
    best_score = 0.0
    for alias in SERVICE_ALIASES:
        score = SequenceMatcher(None, key, alias).ratio()
        if score > best_score:
            best_match = alias
            best_score = score
    if best_score >= 0.88:
        return SERVICE_ALIASES.get(best_match, best_match)
    return key


def canonical_service_name(service_name: str) -> str:
    key = normalize_service_key(service_name)
    return CANONICAL_SERVICE_NAMES.get(key, prettify_service_name(service_name))


def prettify_service_name(service_name: str) -> str:
    if not service_name.strip():
        return ""
    if service_name.isupper():
        return service_name.strip()
    return service_name.strip().title()


def cleanup_service_name(text: str) -> str:
    cleaned = text
    for pattern in FILLER_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[,:;]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .-|")

