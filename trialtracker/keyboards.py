from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def draft_preview_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Все верно", callback_data="draft:save")
    builder.button(text="Исправить", callback_data="draft:edit")
    builder.adjust(2)
    return builder.as_markup()


def duplicate_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Заменить старую", callback_data="dup:replace")
    builder.button(text="Сохранить обе", callback_data="dup:create")
    builder.button(text="Отмена", callback_data="dup:cancel")
    builder.adjust(1)
    return builder.as_markup()


def reminder_keyboard(trial_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Уже отменил", callback_data=f"trial:cancel:{trial_id}")
    builder.button(text="Напомнить позже", callback_data=f"trial:snooze:{trial_id}")
    builder.button(text="Оставляю", callback_data=f"trial:keep:{trial_id}")
    builder.adjust(1)
    return builder.as_markup()
