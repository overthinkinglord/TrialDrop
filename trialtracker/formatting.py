from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable
from zoneinfo import ZoneInfo


CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "RUB": "₽",
}


def format_money(amount_minor: int, currency_code: str) -> str:
    amount = (Decimal(amount_minor) / Decimal("100")).quantize(Decimal("0.01"))
    symbol = CURRENCY_SYMBOLS.get(currency_code, currency_code + " ")
    if amount == amount.to_integral():
        amount_text = str(int(amount))
    else:
        amount_text = format(amount, "f").rstrip("0").rstrip(".")
    if symbol.endswith(" "):
        return f"{symbol}{amount_text}"
    return f"{symbol}{amount_text}"


def format_local_datetime(iso_value: str, timezone_name: str) -> str:
    dt = datetime.fromisoformat(iso_value)
    local_dt = dt.astimezone(ZoneInfo(timezone_name))
    return local_dt.strftime("%d.%m %H:%M")


def format_trial_line(trial: dict, timezone_name: str) -> str:
    return (
        f"{trial['service_name']} — {format_money(trial['amount_minor'], trial['currency_code'])} "
        f"— {format_local_datetime(trial['billing_at'], timezone_name)}"
    )


def format_saved_totals(rows: Iterable[dict]) -> str:
    totals = list(rows)
    if not totals:
        return "0"
    return " · ".join(format_money(row["total_minor"], row["currency_code"]) for row in totals)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

