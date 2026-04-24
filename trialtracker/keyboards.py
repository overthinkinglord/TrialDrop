from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


TIMEZONE_OPTIONS = [
    ("Europe/Berlin", "Europe/Berlin"),
    ("UTC", "UTC"),
    ("Europe/London", "Europe/London"),
    ("America/New_York", "America/New_York"),
    ("Asia/Tbilisi", "Asia/Tbilisi"),
]


def timezone_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, value in TIMEZONE_OPTIONS:
        builder.button(text=label, callback_data=f"tz:{value}")
    builder.adjust(1)
    return builder.as_markup()


def draft_preview_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Сохранить", callback_data="draft:save")
    builder.button(text="Исправить", callback_data="draft:edit")
    builder.adjust(2)
    return builder.as_markup()


def duplicate_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Заменить старый", callback_data="dup:replace")
    builder.button(text="Создать еще один", callback_data="dup:create")
    builder.button(text="Отмена", callback_data="dup:cancel")
    builder.adjust(1)
    return builder.as_markup()


def reminder_keyboard(trial_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Уже отменил", callback_data=f"trial:cancel:{trial_id}")
    builder.button(text="Напомнить через 3ч", callback_data=f"trial:snooze:{trial_id}")
    builder.button(text="Оставляю", callback_data=f"trial:keep:{trial_id}")
    builder.adjust(1)
    return builder.as_markup()
